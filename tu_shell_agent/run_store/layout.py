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
