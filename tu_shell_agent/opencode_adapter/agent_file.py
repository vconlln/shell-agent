"""生成项目级 opencode agent 定义（规格 §7.2）。

安全模型不押在总键上：源码级调研只确认了具体权限键，因此逐键显式 deny。
"""

from __future__ import annotations

from pathlib import Path

from ..orchestrator.prompt import SYSTEM_RULES

AGENT_NAME = "tu-shell-writer"

_DENY_KEYS = (
    "bash",
    "edit",
    "glob",
    "grep",
    "list",
    "lsp",
    "skill",
    "task",
    "todowrite",
    "question",
    "webfetch",
    "websearch",
    "doom_loop",
)


def _to_posix(path: str) -> str:
    """Windows 路径在 YAML 里容易被转义，统一转成正斜杠并去掉尾部斜杠。"""
    return path.replace("\\", "/").rstrip("/")


def render_agent_file(run_dir: str, model: str | None = None) -> str:
    run = _to_posix(run_dir)
    if not run:
        # 不能静默生成：空 run_dir 会让 read 白名单退化成 "/**": allow，
        # 而它比 "*": deny 更具体 → 覆盖默认拒绝 → 整盘可读。宁可报错，不能放行。
        raise ValueError("run_dir 不能为空：否则 read 白名单会退化成 /**（整盘可读）")
    lines = [
        "---",
        "description: 把方案文档实现进给定的 shell 模板骨架；只读运行目录，不写文件、不执行命令。",
        "mode: primary",
    ]
    if model:
        lines.append(f"model: {model}")
    lines.append("permission:")
    lines.extend(f"  {key}: deny" for key in _DENY_KEYS)
    lines.extend(
        [
            "  external_directory: deny",
            "  read:",
            '    "*": deny',
            f'    "{run}/**": allow',
            "---",
            SYSTEM_RULES,
            "",
        ]
    )
    return "\n".join(lines)


def write_agent_file(run_dir: str, model: str | None = None) -> str:
    content = render_agent_file(run_dir, model)  # 先渲染：空 run_dir 要在落盘/建目录之前就抛错
    path = Path(run_dir, ".opencode", "agents", f"{AGENT_NAME}.md")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return str(path)
