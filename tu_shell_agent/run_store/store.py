"""每轮产物落盘。script.sh 在运行目录根与 attempts/<n>/ 双写，便于回放。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ..template_store.render import to_lf
from .layout import attempt_dir


class RunStore:
    def __init__(self, run_dir: str) -> None:
        self.run_dir = run_dir

    def init(self) -> None:
        Path(self.run_dir, ".opencode", "agents").mkdir(parents=True, exist_ok=True)

    def write_script(self, round_no: int, script: str) -> str:
        text = to_lf(script)
        round_path = Path(attempt_dir(self.run_dir, round_no))
        round_path.mkdir(parents=True, exist_ok=True)
        (round_path / "script.sh").write_text(text, encoding="utf-8")
        root_path = Path(self.run_dir) / "script.sh"
        root_path.write_text(text, encoding="utf-8")
        return str(root_path)

    def write_attempt(self, round_no: int, files: dict[str, str]) -> None:
        target = Path(attempt_dir(self.run_dir, round_no))
        target.mkdir(parents=True, exist_ok=True)
        for name, content in files.items():
            (target / name).write_text(content, encoding="utf-8")

    def write_meta(self, patch: dict[str, Any]) -> None:
        path = Path(self.run_dir) / "meta.json"
        # 与 write_script / write_attempt 保持一致的自我修复：三者都不该假定调用方先调过 init()。
        path.parent.mkdir(parents=True, exist_ok=True)
        current: dict[str, Any] = {}
        if path.exists():
            current = json.loads(path.read_text(encoding="utf-8"))
        current.update(patch)
        path.write_text(
            json.dumps(current, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
