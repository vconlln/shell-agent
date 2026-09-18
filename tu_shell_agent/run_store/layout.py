"""运行目录布局（规格 §10）。"""

from __future__ import annotations

import secrets
from datetime import datetime, timezone
from pathlib import Path

AGENT_RELATIVE_PATH = ".opencode/agents/tu-shell-writer.md"


def make_run_id(at: datetime | None = None, salt: str | None = None) -> str:
    """时间戳用 UTC：否则同一条测试在不同时区会得到不同前缀。"""
    moment = at or datetime.now(timezone.utc)
    suffix = salt if salt is not None else secrets.token_hex(2)
    return f"{moment:%Y%m%d-%H%M%S}-{suffix}"


def run_dir_for(run_root: str, run_id: str) -> str:
    return str(Path(run_root) / run_id)


def attempt_dir(run_dir: str, round_no: int) -> str:
    return str(Path(run_dir) / "attempts" / str(round_no))


def error_evidence(attempt_dir: str) -> list[tuple[str, str]]:
    """取某一轮 attempts/<n>/ 里的错误证据（*-error.txt），返回 [(文件名, 内容)]。

    这些文件是"这次为什么没跑成"的唯一落盘证据（例如无凭据时上游拒绝生成的那句原话）。
    界面（实时收尾与历史回放）都要把它们摊给用户看：只显示"结论：needs_human"等于
    让人对着一个失败结论干瞪眼。

    读不动的文件按空内容跳过（半截写入、编码问题都不该让界面崩）。
    """
    target = Path(attempt_dir)
    if not target.is_dir():
        return []
    found: list[tuple[str, str]] = []
    for path in sorted(target.glob("*-error.txt")):
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        if text.strip():
            found.append((path.name, text))
    return found
