"""命令行 agent 适配器：用子进程驱动 Claude Code 风格的 CLI，实现 `OpencodePort` 的同一套方法。

为什么要有它：这个应用的引擎只认 `OpencodePort` 这六个方法，而"用哪个 agent 生成脚本"不该被
写死成 opencode。命令行后端（Claude Code 及其改造版）在非交互模式下能给出同一件事：一段脚本。
差别只在通道上 —— opencode 走 HTTP + JSON schema，命令行后端走"一次进程 + 一段文本"，
所以结构约束由 `orchestrator.cli_contract` 的文本契约补上。

谁在什么时候被杀：命令行 agent 会真的动文件系统（Claude Code 属于自主型 agent），因此
**默认禁止执行任何命令**：执行类工具整类进 `--disallowedTools`，写入类工具同理，权限模式留在
需要批准的档位（非交互进程里没人能批准，等于不放行）。**引擎才是唯一的执行者，agent 只写不跑。**

分层：子进程只在本模块（适配器层）起；`ui/**` 通过 `ui/engine_worker.py` 的 QThread 间接调用，
不直接碰 subprocess。
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable, Sequence

from ..orchestrator.cli_contract import (
    CLI_SYSTEM_RULES,
    CliContractError,
    parse_generated_script,
    with_output_instructions,
)
from ..shell_toolchain.execute import kill_tree
from .invocation import invocation_argv
from .invocation import resolve_command as invocation_resolve
from ..types import DetectedTool, GeneratedScript

# 版本探测的默认超时：`claude --version` 是纯本地操作，20s 已经很宽松。
DEFAULT_PROBE_TIMEOUT_S = 20.0

# 版本号取"看起来像版本的那一段"（`2.1.112 (Claude Code)` 与 `1.18.31` 都能取到）。
_VERSION = re.compile(r"\d+\.\d+(?:\.\d+)?")


@dataclass(frozen=True, slots=True)
class CliSpec:
    """一个命令行后端的旗标约定。

    单独做成数据而不是在适配器里 `if backend == ...` 分支：后端之间的差异只有"命令名与几个
    旗标"，把差异放在数据里，加一个新后端就是加一份常量，适配器一行都不用改。
    """

    prompt_flag: str = "-p"
    output_format_flag: str = "--output-format"
    output_format: str = "stream-json"
    verbose_flag: str = "--verbose"
    session_new_flag: str = "--session-id"
    session_resume_flag: str = "--resume"
    model_flag: str = "--model"
    system_prompt_flag: str = "--append-system-prompt"
    allowed_tools_flag: str = "--allowedTools"
    disallowed_tools_flag: str = "--disallowedTools"
    permission_mode_flag: str = "--permission-mode"
    permission_mode: str = "default"
    denied_tools: tuple[str, ...] = ()
    allowed_tools: tuple[str, ...] = ()
    extra_args: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class ProbeResult:
    """一次"这个后端的命令能不能用"的探测结果。"""

    tool: DetectedTool | None = None
    message: str = ""

    @property
    def ok(self) -> bool:
        return self.tool is not None

    def detail(self) -> str:
        """一行可读结论（界面提示行直接用这句）。"""
        if self.tool is None:
            return self.message
        return f"命令可用：{self.tool.path}（版本 {self.tool.version}）"


def resolve_command(command: str) -> str | None:
    """把用户填的命令解析成可执行文件的绝对路径；找不到返回 None。

    为什么不直接交给 `subprocess` 去撞 FileNotFoundError：探测与运行两条路都需要"找不到"
    这个信息，而且要给用户"填完整路径"这种可操作的建议，所以在进子进程之前先解析一次。

    实现委托给 `invocation.resolve_command`：Windows 上除了 PATH 还会去常见安装目录找一遍
    （GUI 进程的 PATH 可能比终端少几条 —— 用户"明明装了却找不到"多半就是这个）。
    """
    text = (command or "").strip()
    if not text:
        return None
    return invocation_resolve(text)


# 探测版本时依次尝试的参数：不是所有 CLI 都认 `--version`（有的只认 `-v`，有的只打帮助），
# 而"能不能用"与"能不能问出版本"是两件事 —— 问不出版本不该被判成"没装"。
_PROBE_SEQUENCE: tuple[tuple[str, ...], ...] = (
    ("--version",),
    ("-v",),
    ("version",),
    ("--help",),
)


def _probe_args(version_args: Sequence[str]) -> list[tuple[str, ...]]:
    """要依次尝试的参数组合：用户/后端声明的那个排在前面，其余作为兜底。"""
    sequence: list[tuple[str, ...]] = [tuple(version_args)]
    for candidate in _PROBE_SEQUENCE:
        if candidate not in sequence:
            sequence.append(candidate)
    return sequence


def probe_version(
    command: str,
    version_args: Sequence[str] = ("--version",),
    *,
    timeout_s: float = DEFAULT_PROBE_TIMEOUT_S,
    install_hint: str = "",
    runner: Callable[..., Any] | None = None,
) -> tuple[DetectedTool | None, str]:
    """跑一次版本命令，返回 (探测结果, 失败原因)。

    失败原因直接可给用户看：找不到命令时带上安装建议（`install_hint`），
    退出码非零时带上它最后一行输出 —— 只看"退出码 1"没人知道该做什么。

    三条"别把能用的东西判成没装"的规则（用户实测踩到过"我装了 codeagent 却检测不到"）：

    1. **不止试 `--version`**：`-v` / `version` / `--help` 依次兜底 —— 不是每个 CLI 都认
       `--version`，而"能不能用"与"能不能问出版本"是两件事；
    2. **能跑起来就算找到了**：命令退出码为 0、输出里没有版本号时，返回"版本未知"而不是失败
       （版本未知不影响它能不能干活）；
    3. **stdin 接空设备**：探测是无人值守的，若 CLI 想读输入（读 TTY、等确认）就会一直挂到超时 ——
       在打包好的 GUI 里那是"点了检测没反应"。给它一个空的 stdin，需要读输入的命令会立刻拿到 EOF。
    """
    resolved = resolve_command(command)
    if resolved is None:
        return None, (f"找不到命令 {command or '（未填写）'}：{install_hint}".rstrip("："))
    run = runner if runner is not None else subprocess.run
    failures: list[str] = []
    for args in _probe_args(version_args):
        label = f"`{command} {' '.join(args)}`"
        try:
            completed = run(
                invocation_argv(command, args),
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout_s,
                check=False,
                stdin=subprocess.DEVNULL,
                env={**os.environ, "NO_COLOR": "1", "LC_ALL": "C", "LANG": "C"},
            )
        except subprocess.TimeoutExpired:
            failures.append(f"{label} 超过 {timeout_s:.0f}s 没有返回")
            continue
        except OSError as error:  # 权限不够、不是可执行文件等
            return None, f"无法执行 {resolved}：{error}"
        output = f"{completed.stdout or ''}\n{completed.stderr or ''}".strip()
        if completed.returncode != 0:
            tail = output.splitlines()[-1] if output else "无输出"
            failures.append(f"{label} 退出码 {completed.returncode}：{tail}")
            continue
        match = _VERSION.search(output)
        if match is not None:
            return DetectedTool(path=resolved, version=match.group(0)), ""
        # 能跑、也有输出，只是没版本号：算找到了（版本未知），不要再被判成"检测不到"
        return DetectedTool(path=resolved, version="未识别"), ""
    first = failures[0] if failures else "没有可用的探测参数"
    extra = f"（已依次尝试 {'、'.join(' '.join(args) for args in _probe_args(version_args))}）"
    return None, f"{first}{extra}"


def explain_cli_error(text: str, command: str = "") -> str:
    """把命令行 agent 的已知报错翻译成"该怎么办"；认不出来返回空串。

    与 `opencode_adapter/errors.py` 同一个思路（只映射**已知且可操作**的情形），
    但那条路只认 opencode 的措辞，两个模块各管各的后端，互不改写对方的行为。
    """
    lowered = (text or "").lower()
    name = command or "该命令"
    if any(marker in lowered for marker in ("not logged in", "unauthorized", "authentication",
                                            "invalid api key", "api key", "credentials", "401")):
        return (
            f"处理：先在终端里运行一次 `{name}` 完成登录或配置 API key，再重新发起本次生成。"
        )
    if "no conversation found" in lowered or "session not found" in lowered:
        return (
            "处理：本机没有这段会话（可能已被清理）。请重新点「开始」建立新会话，"
            "不要用「继续修复」。"
        )
    if "must be a valid uuid" in lowered or "invalid session id" in lowered:
        return "处理：会话 id 不合法；请重新点「开始」建立新会话。"
    if "permission" in lowered and "denied" in lowered:
        return (
            "处理：本应用刻意禁止 agent 执行命令与写文件（引擎是唯一的执行者）。"
            "若这一轮因此失败，请检查是否把执行类工具从禁用清单里放开了。"
        )
    return ""


@dataclass(slots=True)
class _StreamState:
    """一次进程调用的中间状态（读取线程与主线程共用一个实例）。"""

    parts: list[str] = field(default_factory=list)
    result_text: str = ""
    cancelled: bool = False
    timed_out: bool = False


class _DeltaExtractor:
    """把 stream-json 的 JSON 行变成"给界面看的增量文本"。

    实测形状（claude 2.1.112）里同一条消息会被发多次（先 thinking 块，再 text 块），
    而 `--include-partial-messages` 打开后还会发真正的增量帧。两种都要认，且不能重复：

    - `assistant` 事件带上这一条消息**到目前为止**的全文 → 与已发出的部分比前缀，只发新增的尾巴；
    - `stream_event` 里的 `content_block_delta` 是真正的增量 → 直接发；
    - `result` 事件带全文 → 只留作兜底（助手文本一个都没收到时才用它），否则会把整段重复一遍；
      它同时带 `is_error`（本轮是否失败）与 `session_id`（与我们要的会话交叉核对）；
    - 认不出来的事件（`system`、未来的新类型、非 JSON 的日志行）一律忽略。

    "未知事件不许把这一轮弄崩"是硬要求：命令行 agent 的版本一升级就可能多发几种事件，
    解析器不能比它更脆。
    """

    def __init__(self) -> None:
        self._message_id = ""
        self._emitted = ""
        self.result_text = ""
        # `result` 事件里的失败标志：退出码是 0 但这一轮其实报错的情形要靠它认出来。
        self.is_error = False
        # 命令行 agent 报回来的会话 id。与我们请求的那个不一致说明它另开了会话（例如 --resume
        # 落到了新会话），后续轮次会找不到上下文，所以由调用方给出一条提示。
        self.session_id = ""

    def feed(self, line: str) -> str | None:
        text = line.strip()
        if not text or not text.startswith("{"):
            return None                      # 非 JSON 行（日志、进度条）直接丢
        try:
            event = json.loads(text)
        except json.JSONDecodeError:
            return None
        if not isinstance(event, dict):
            return None

        session_id = event.get("session_id")
        if isinstance(session_id, str) and session_id:
            self.session_id = session_id

        kind = event.get("type")
        if kind == "assistant":
            return self._from_assistant(event)
        if kind == "stream_event":
            return self._from_stream_event(event)
        if kind == "result":
            result = event.get("result")
            if isinstance(result, str):
                self.result_text = result
            self.is_error = bool(event.get("is_error"))
            return None
        return None

    def _from_assistant(self, event: dict[str, Any]) -> str | None:
        message = event.get("message")
        if not isinstance(message, dict):
            return None
        message_id = str(message.get("id") or "")
        if message_id != self._message_id:
            self._message_id = message_id
            self._emitted = ""
        whole = _message_text(message)
        if not whole:
            return None
        # 同一条消息被重发时带的是全文：只发多出来的尾巴，避免界面把同一段话打印两遍。
        delta = whole[len(self._emitted):] if whole.startswith(self._emitted) else whole
        self._emitted = whole
        return delta or None

    def _from_stream_event(self, event: dict[str, Any]) -> str | None:
        inner = event.get("event")
        if not isinstance(inner, dict):
            return None
        if inner.get("type") != "content_block_delta":
            return None
        delta = inner.get("delta")
        if not isinstance(delta, dict):
            return None
        text = delta.get("text")
        if not isinstance(text, str) or not text:
            return None
        # 增量帧与 assistant 全文帧可能同时开着：把增量并进 _emitted，之后全文帧才不会重发。
        self._emitted += text
        return text


def _message_text(message: dict[str, Any]) -> str:
    """取一条 assistant 消息里所有 text 块拼起来的文本（thinking / tool_use 块不算）。"""
    content = message.get("content")
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""
    parts: list[str] = []
    for block in content:
        if not isinstance(block, dict):
            continue
        if block.get("type") not in (None, "text"):
            continue
        text = block.get("text")
        if isinstance(text, str):
            parts.append(text)
    return "".join(parts)


class CliAgentAdapter:
    """`OpencodePort` 的命令行实现（Claude Code 风格 CLI）。

    会话语义与 opencode 那条路刻意保持一致：

    - `start()` 生成一个 UUID 作为会话 id（命令行那边要求 `--session-id` 是合法 UUID），
      并记下"这段会话还没有建立"；下一次调用用 `--session-id` 建立它；
    - `resume()` 记录工作目录与模型，下一次调用用 `--resume` 接上既有会话；
    - 之后每一轮都用 `--resume`：这个 CLI 一次进程只处理一轮对话，进程退出后靠 resume 续上。
    """

    def __init__(
        self,
        command: str,
        *,
        spec: CliSpec | None = None,
        extra_args: Iterable[str] = (),
        note: Callable[[str], None] | None = None,
    ) -> None:
        self._command = command
        self._spec = spec or CliSpec()
        # extra_args 是构造参数级的追加（用户想加自己的旗标时用），与 spec.extra_args 一并拼上：
        # 一个是"这个后端固有的是什么"，一个是"这台机器上想额外加什么"，来源不同。
        self._extra_args = (*self._spec.extra_args, *tuple(extra_args))
        self._note = note or (lambda _message: None)
        self._run_dir = ""
        self._model = ""
        self._session_id = ""
        self._pending_new_session = False
        self._process: subprocess.Popen | None = None
        self._lock = threading.Lock()
        # `abort()` 是**从别的线程**被调的（界面按下取消、关窗收尾），所以它留下的痕迹要让
        # 正在跑的 `_run` 能看见：这就是这个事件的作用。
        self._abort = threading.Event()

    # ── 生命周期 ────────────────────────────────────────────────────────
    def start(self, run_dir: str, agent_name: str = "tu-shell-writer", model: str | None = None) -> str:
        """新建一段会话：id 由**我们**生成（CLI 要求 `--session-id` 是合法 UUID）。

        `agent_name` 只在 opencode 那条路上有意义（它要写 agent 定义文件），命令行后端没有
        对应的东西，所以这里显式忽略 —— 两个端口的签名保持一致比"多一个用不上的参数"重要。
        """
        del agent_name
        session_id = str(uuid.uuid4())
        self._run_dir = str(run_dir or "")
        self._model = str(model or "")
        self._session_id = session_id
        self._pending_new_session = True
        return session_id

    def resume(self, run_dir: str, model: str | None = None) -> None:
        """续跑入口：记录工作目录与模型，下一轮用 `--resume` 接上既有会话。

        这里**不**收到会话 id（端口签名如此）：真正的 id 由编排层在 `generate()` 里给出，
        本方法的职责只是"把工作目录与模型换到这一次运行上"。
        """
        if run_dir:
            self._run_dir = str(run_dir)
        if model:
            self._model = str(model)
        self._pending_new_session = False

    def dispose(self) -> None:
        """收尾：杀掉还在跑的进程。**不抛异常** —— 它出现在 finally 与关窗路径上。

        同时打上"已放弃"的标记：在飞的那一轮被这里杀掉时，退出码会是 -9，
        只看退出码会把它报成一句莫名其妙的"退出码 -9"，而它其实是"这次调用被放弃了"。
        """
        self._abort.set()
        self._kill_process()

    # ── 生成与对话 ──────────────────────────────────────────────────────
    def generate(
        self,
        session_id: str,
        message: str,
        schema: dict[str, Any],
        timeout_ms: int,
        on_delta: Callable[[str], None] | None = None,
        cancel: Any = None,
    ) -> GeneratedScript:
        """一轮生成：把文本契约追加到消息上，再把回复解析成 `GeneratedScript`。

        `schema` 参数被刻意忽略：命令行后端没有结构化输出开关（这是本适配器存在的原因之一）。
        编排层仍然按同一个签名调用它，结构约束改由 `cli_contract` 承担。
        """
        del schema
        prompt = with_output_instructions(message)
        # 硬规则走系统提示（与 opencode 的 agent 定义同源），输出格式要求走用户消息：
        # 前者是"你是谁、什么不许做"，后者是"这次要按什么格式回"，混在一起会让模型把格式要求
        # 当成可以商量的建议。
        reply = self._run(
            session_id=session_id,
            prompt=prompt,
            timeout_ms=timeout_ms,
            on_delta=on_delta,
            cancel=cancel,
            system_prompt=CLI_SYSTEM_RULES,
        )
        if not reply.strip():
            raise RuntimeError(
                f"`{self._command}` 没有输出任何助手文本（stream-json 里没有 assistant 内容）"
            )
        try:
            return parse_generated_script(reply)
        except CliContractError as error:
            raise RuntimeError(
                f"{error}\n"
                "处理：本轮会按契约失败回灌重试；若连续两轮都缺少标记，"
                "说明该后端没有遵守输出格式要求，可在「设置 → 后端 agent」中换一个后端。"
            ) from error

    def chat(
        self,
        session_id: str,
        message: str,
        timeout_ms: int,
        on_delta: Callable[[str], None] | None = None,
        cancel: Any = None,
        system_preamble: str = "",
        model: str = "",
    ) -> str:
        """自由对话：不带输出格式要求，返回模型的纯文本回复。

        与 `generate` 的区别和 opencode 那条路逐字一致：`chat` 只是说话，它产出的脚本
        **不会自动执行** —— 要执行必须先进中栏、再走"改后重跑"（shellcheck + 人工确认）。
        `system_preamble` 在这里走 `--append-system-prompt`，正是这个旗标的用途。
        """
        return self._run(
            session_id=session_id,
            prompt=message,
            timeout_ms=timeout_ms,
            on_delta=on_delta,
            cancel=cancel,
            system_prompt=system_preamble,
            model=model,
        )

    def abort(self, session_id: str) -> None:
        """中断这一轮：杀**整棵进程树**。

        只杀直接子进程是不够的：命令行 agent 常常自己再起帮手（子 agent、shell、语言服务），
        留下它们会继续占用运行目录、甚至继续改文件。
        """
        del session_id
        self._abort.set()
        self._kill_process()

    # ── 进程调用 ────────────────────────────────────────────────────────
    def build_argv(
        self,
        *,
        session_id: str,
        model: str = "",
        system_prompt: str = "",
        new_session: bool = False,
    ) -> list[str]:
        """拼出这一次调用的命令行。

        提示词走 stdin 而不是位置参数：方案文档 + 骨架可能有几百 KB，塞进 argv 会在
        Windows 上撞命令行长度上限；而且位置参数跟在 `--disallowedTools` 这类可变长旗标后面，
        可能被当成工具名吃掉。
        """
        spec = self._spec
        argv: list[str] = [*self._extra_args]
        argv += [spec.prompt_flag, spec.output_format_flag, spec.output_format]
        if spec.verbose_flag:
            argv.append(spec.verbose_flag)
        if model:
            argv += [spec.model_flag, model]
        if system_prompt.strip():
            argv += [spec.system_prompt_flag, system_prompt]
        argv += [
            spec.session_new_flag if new_session else spec.session_resume_flag,
            session_id,
        ]
        # 权限相关的旗标放在最后：`--allowedTools` / `--disallowedTools` 是可变长参数，
        # 后面的位置参数会被它们吃掉；放在末尾就不会影响别的旗标。
        if spec.permission_mode_flag and spec.permission_mode:
            argv += [spec.permission_mode_flag, spec.permission_mode]
        if spec.allowed_tools and spec.allowed_tools_flag:
            argv += [spec.allowed_tools_flag, ",".join(spec.allowed_tools)]
        if spec.denied_tools and spec.disallowed_tools_flag:
            argv += [spec.disallowed_tools_flag, ",".join(spec.denied_tools)]
        # 最后统一把"命令 + 参数"转成能起的 argv：Windows 上 npm 装的 CLI 是 `.cmd` 包壳，
        # 必须经 cmd.exe /c 才能起（详见 invocation.py 的说明）。
        return invocation_argv(self._command, argv)

    def _take_new_session(self, session_id: str) -> bool:
        """这一次调用是"建立新会话"还是"续上既有会话"？问完即改状态。

        只有 `start()` 亲手建出来的那段会话、且还没发出过一句话时，才用 `--session-id`；
        其余（续跑、对话恢复、第二轮以后）都是 `--resume`。
        """
        with self._lock:
            fresh = self._pending_new_session and session_id == self._session_id
            self._pending_new_session = False
            return fresh

    def _run(
        self,
        *,
        session_id: str,
        prompt: str,
        timeout_ms: int,
        on_delta: Callable[[str], None] | None,
        cancel: Any,
        system_prompt: str = "",
        model: str = "",
    ) -> str:
        """起一次子进程、读流、按超时/取消收尾；返回助手文本（解析失败由调用方决定）。"""
        if not self._command.strip():
            raise RuntimeError(
                "没有可执行的命令：请在「设置 → 后端 agent」中填写该后端的命令。"
            )
        resolved = resolve_command(self._command)
        if resolved is None:
            raise RuntimeError(
                f"找不到命令 {self._command}：请确认它已安装并在 PATH 中，"
                "或在「设置 → 后端 agent」中填写完整路径。"
            )
        if self._cancelled(cancel):
            raise RuntimeError("已取消")

        argv = self.build_argv(
            session_id=session_id,
            model=model or self._model,
            system_prompt=system_prompt,
            new_session=self._take_new_session(session_id),
        )
        # 上一轮的 abort 标记要在这里清掉：不清的话，下一次 generate 会在进程正常结束后
        # 把"已取消"当成这一轮的结果。
        self._abort.clear()
        popen_kwargs: dict[str, Any] = {
            "cwd": self._run_dir or None,
            "stdin": subprocess.PIPE,
            "stdout": subprocess.PIPE,
            "stderr": subprocess.PIPE,
            "text": True,
            "encoding": "utf-8",
            "errors": "replace",
            "env": {**os.environ, "NO_COLOR": "1"},
        }
        # 必须让子进程活在自己的进程组里，理由与 shell_toolchain.execute 完全相同（那里是实测结论）：
        # kill_tree 在 POSIX 走 os.killpg(os.getpgid(pid), SIGKILL)，而 Popen 默认让子进程继承
        # 调用方的进程组 —— 少了这一行，取消/超时会把我们自己（CLI 或界面进程）一起杀掉。
        if os.name == "nt":
            popen_kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
        else:
            popen_kwargs["start_new_session"] = True

        state = _StreamState()
        extractor = _DeltaExtractor()
        stderr_lines: list[str] = []
        lock = threading.Lock()
        try:
            process = subprocess.Popen(argv, **popen_kwargs)
        except OSError as error:
            raise RuntimeError(f"无法启动 {resolved}：{error}") from error
        with self._lock:
            self._process = process

        def write_prompt() -> None:
            """把提示词写进 stdin 并关闭它。

            单独一个线程：先写后读会死锁（提示词大到填满管道缓冲区时，子进程还没读完就没法
            输出，而我们在等它的输出）。
            """
            try:
                assert process.stdin is not None
                process.stdin.write(prompt)
                process.stdin.close()
            except (BrokenPipeError, ValueError, OSError):
                pass                          # 子进程提前退出：不算错误，后面按退出码判

        def read_stdout() -> None:
            assert process.stdout is not None
            for line in process.stdout:
                delta = extractor.feed(line)
                if delta:
                    with lock:
                        state.parts.append(delta)
                    if on_delta is not None:
                        on_delta(delta)
            result = extractor.result_text
            if result:
                with lock:
                    state.result_text = result

        def read_stderr() -> None:
            assert process.stderr is not None
            for line in process.stderr:
                with lock:
                    stderr_lines.append(line)

        threads = [
            threading.Thread(target=write_prompt, daemon=True),
            threading.Thread(target=read_stdout, daemon=True),
            threading.Thread(target=read_stderr, daemon=True),
        ]
        for thread in threads:
            thread.start()

        deadline = time.monotonic() + timeout_ms / 1000.0
        while process.poll() is None:
            if self._cancelled(cancel):
                state.cancelled = True
                self._kill_process()
                break
            if time.monotonic() > deadline:
                state.timed_out = True
                self._kill_process()
                break
            time.sleep(0.05)

        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            self._kill_process()
        for thread in threads:
            thread.join(timeout=5)
        with self._lock:
            self._process = None
            parts = list(state.parts)
            stderr_text = "".join(stderr_lines)

        if state.cancelled or self._abort.is_set():
            # 取消不是失败：与 opencode 那条路同一句话，编排层据此判 `cancelled` 终态。
            # `_abort` 覆盖的是"从别的线程调 abort()"这条路径 —— 那时进程被 SIGKILL，
            # 退出码是 -9，只看退出码会把它报成一句莫名其妙的"退出码 -9"。
            raise RuntimeError("已取消")
        if state.timed_out:
            raise RuntimeError(
                f"`{self._command}` 在 {timeout_ms / 1000:.0f}s 内没有返回，已终止该进程树。"
                "处理：在「设置 → 默认运行参数」中调大生成超时，或先手工确认这个命令能单独跑通。"
            )
        self._check_session_id(extractor.session_id, session_id)
        if extractor.is_error:
            # 退出码有时仍是 0 而 `result` 事件明说这一轮失败了（上游拒绝、被权限挡住等），
            # 只看退出码会把这种失败当成"没产出脚本"，用户拿到的理由就丢了一半。
            reason = _tail_lines(extractor.result_text) or _tail_lines(stderr_text) or "无输出"
            hint = explain_cli_error(reason + stderr_text, self._command)
            raise RuntimeError(
                f"`{self._command}` 报告本轮失败：{reason}" + (f"\n{hint}" if hint else "")
            )
        if process.returncode != 0:
            detail = _tail_lines(stderr_text) or _tail_lines("".join(parts)) or "无输出"
            hint = explain_cli_error(detail + stderr_text, self._command)
            raise RuntimeError(
                f"`{self._command}` 退出码 {process.returncode}：{detail}"
                + (f"\n{hint}" if hint else "")
            )
        text = "".join(parts)
        if not text and extractor.result_text:
            # 兜底：只发了 result 事件（没有 assistant 事件）的版本，用 result 里的全文。
            text = extractor.result_text
        return text

    def _check_session_id(self, reported: str, requested: str) -> None:
        """交叉核对会话 id：命令行 agent 报回来的与我们请求的不是同一个时说一句。

        不判失败：`--resume` 在某些版本上会派生新会话（那是它的自由），而这一轮的产出仍然可用。
        但**必须留痕** —— 会话悄悄换了的话，下一轮的"继续修复"就接不上上下文，
        而那时用户看到的现象只是"模型像失忆了一样"，无从判断原因。
        """
        if not reported or not requested or reported == requested:
            return
        self._note(f"agent 返回的会话 id 与请求的不一致（请求 {requested}，返回 {reported}）")

    def _kill_process(self) -> None:
        """杀掉当前进程树；顺带清掉引用。任何异常都不许逃出去（它出现在收尾路径上）。"""
        with self._lock:
            process = self._process
            self._process = None
        if process is None or process.poll() is not None:
            return
        try:
            if process.pid is not None:
                kill_tree(process.pid)
        except Exception as error:  # noqa: BLE001 - 收尾失败不该盖住真正的失败原因
            self._note(f"终止 {self._command} 失败：{error}")

    @staticmethod
    def _cancelled(cancel: Any) -> bool:
        is_set = getattr(cancel, "is_set", None)
        return bool(callable(is_set) and is_set())


def _tail_lines(text: str, limit: int = 5) -> str:
    lines = [line.strip() for line in (text or "").splitlines() if line.strip()]
    return "\n".join(lines[-limit:])


__all__ = [
    "CliAgentAdapter",
    "CliSpec",
    "ProbeResult",
    "explain_cli_error",
    "probe_version",
    "resolve_command",
]
