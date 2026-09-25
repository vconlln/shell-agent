"""内置 agent 的**只读工具**：让模型能自己看运行目录里的东西，但碰不到别的、也改不了任何东西。

用户要求与 opencode 功能对齐（"最少跟 opencode 的功能保持一致"），而 opencode 那条路的
agent 是允许 `Read` / `Glob` / `Grep` 的（只读），`Bash` / `Write` / `Edit` 全部 deny。
之前内置 agent 一个工具都没有 —— 模型只能看到提示词里那段方案文本，不能翻运行目录里的
骨架、模板或上一轮的产物。这里补上这一层，并且**范围比 opencode 更窄**：

- 只能读**运行目录**（本应用自己的产物目录：`plan.md` / `template.sh` / `attempts/…`）；
- 路径必须落在该目录内 —— `..`、绝对路径、符号链接指向外面，一律拒绝；
- 只有两个动作：`list_dir`（列目录）与 `read_file`（读文本）；没有写、没有执行、没有网络；
- 读有上限（单文件 64KB、一次列 500 条），避免把上下文一次性灌满；
- 执行的权限仍然只属于引擎（`orchestrator` → shellcheck → 人工确认 → bash），
  这条与 opencode 那条路完全一致，不因为"模型能读了"而放宽。

工具调用的协议两种风格各一套（OpenAI 的 `tools` + `tool_calls`，Anthropic 的
`tools` + `tool_use`/`tool_result`），形状见 `tool_specs()`。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

# 上限：宁可让模型少看一点，也不要把上下文一次灌满（超了它会自己再读一次或换小范围）
MAX_FILE_BYTES = 64 * 1024
MAX_ENTRIES = 500
# 明显是二进制的内容不返回（模型读它没有意义，还会污染上下文）
_BINARY_SAMPLE = 4096

LIST_TOOL = "list_dir"
READ_TOOL = "read_file"

_TOOL_DEFS: tuple[dict[str, Any], ...] = (
    {
        "name": LIST_TOOL,
        "description": "列出运行目录里的文件与子目录（相对路径、大小）。用于确认有哪些产物。",
        "parameters": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "相对运行目录的路径，留空表示运行目录本身",
                }
            },
            "required": [],
        },
    },
    {
        "name": READ_TOOL,
        "description": (
            "读取运行目录里的一个文本文件（最多 64KB）。"
            "用于看方案、模板骨架、上一轮脚本或 shellcheck 报告。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "相对运行目录的文件路径"},
            },
            "required": ["path"],
        },
    },
)


def tool_specs(style: str = "openai") -> list[dict[str, Any]]:
    """按接口风格给出工具声明（OpenAI 的 `function` 包装与 Anthropic 的形状不同）。"""
    if style == "anthropic":
        return [
            {
                "name": item["name"],
                "description": item["description"],
                "input_schema": item["parameters"],
            }
            for item in _TOOL_DEFS
        ]
    return [
        {"type": "function", "function": item} for item in _TOOL_DEFS
    ]


@dataclass(frozen=True, slots=True)
class ToolCall:
    """模型请求的一次工具调用。"""

    id: str
    name: str
    arguments: dict[str, Any]


class ToolError(RuntimeError):
    """工具调用失败（路径越界、文件不存在、超出上限）—— 内容会回给模型，让它改。"""


class WorkspaceReader:
    """把"模型能读什么"收敛到一个目录里。

    所有路径都经过 `_resolve()`：先按根目录拼接，再 `resolve()` 取真实路径，最后检查它是否
    仍在根目录之内。**符号链接也算**（`resolve()` 会把链接展开），所以"链接指向外面"同样被拒绝。
    """

    def __init__(self, root: str, *, max_file_bytes: int = MAX_FILE_BYTES,
                 max_entries: int = MAX_ENTRIES) -> None:
        self.root = Path(root or ".").expanduser().resolve()
        self.max_file_bytes = int(max_file_bytes)
        self.max_entries = int(max_entries)

    # ── 对外 ──────────────────────────────────────────────────────────
    def run(self, call: ToolCall) -> str:
        """执行一次工具调用，返回给模型看的文本（失败也返回文本，不抛出去）。"""
        try:
            if call.name == LIST_TOOL:
                return self.list_dir(str(call.arguments.get("path") or ""))
            if call.name == READ_TOOL:
                return self.read_file(str(call.arguments.get("path") or ""))
            return f"错误：没有名为 {call.name} 的工具（只有 {LIST_TOOL} / {READ_TOOL}）。"
        except ToolError as error:
            return f"错误：{error}"

    def list_dir(self, path: str) -> str:
        target = self._resolve(path or ".")
        if not target.is_dir():
            raise ToolError(f"{path or '.'} 不是目录")
        lines: list[str] = []
        children = sorted(target.iterdir(), key=lambda item: (item.is_file(), item.name))
        for index, child in enumerate(children):
            if index >= self.max_entries:
                lines.append(f"…（还有更多，只列了前 {self.max_entries} 项）")
                break
            try:
                size = child.stat().st_size
            except OSError:
                size = 0
            kind = "/" if child.is_dir() else ""
            lines.append(f"{child.relative_to(self.root).as_posix()}{kind}\t{size}B")
        return "\n".join(lines) or "（空目录）"

    def read_file(self, path: str) -> str:
        target = self._resolve(path)
        if not target.is_file():
            raise ToolError(f"{path} 不是文件（可以先用 {LIST_TOOL} 看看有什么）")
        try:
            size = target.stat().st_size
        except OSError as error:
            raise ToolError(f"{path} 读不到：{error}") from error
        if size > self.max_file_bytes:
            raise ToolError(
                f"{path} 太大（{size} 字节，上限 {self.max_file_bytes}）——"
                "请只读需要的部分文件。"
            )
        try:
            raw = target.read_bytes()
        except OSError as error:
            raise ToolError(f"{path} 读不到：{error}") from error
        if b"\x00" in raw[:_BINARY_SAMPLE]:
            raise ToolError(f"{path} 看起来是二进制文件，读出文本没有意义")
        return raw.decode("utf-8", errors="replace")

    # ── 内部 ──────────────────────────────────────────────────────────
    def _resolve(self, path: str) -> Path:
        text = str(path or "").strip().replace("\\", "/")
        if text.startswith("/") or (len(text) > 1 and text[1] == ":"):
            raise ToolError("只接受相对运行目录的路径（不接受绝对路径）")
        candidate = (self.root / text).resolve()
        if candidate != self.root and self.root not in candidate.parents:
            raise ToolError("路径超出了运行目录（只允许读运行目录里的东西）")
        return candidate
