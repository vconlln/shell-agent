"""每轮产物落盘。script.sh 在运行目录根与 attempts/<n>/ 双写，便于回放。

所有文本都走 `filetext.write_text_lf`：`Path.write_text()` 在 Windows 上会把 \n 翻译成
\r\n，脚本一旦落成 CRLF，shellcheck 会给每一行报 SC1017（error），默认阻断级别下
**每一轮都不会执行** —— Windows 上整个主循环一次都跑不到 succeeded。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ..template_store.render import to_lf
from .layout import attempt_dir
from ..filetext import write_text_lf


class RunStore:
    def __init__(self, run_dir: str) -> None:
        self.run_dir = run_dir

    def init(self) -> None:
        Path(self.run_dir, ".opencode", "agents").mkdir(parents=True, exist_ok=True)

    def write_script(self, round_no: int, script: str) -> str:
        text = to_lf(script)
        round_path = Path(attempt_dir(self.run_dir, round_no))
        round_path.mkdir(parents=True, exist_ok=True)
        write_text_lf(round_path / "script.sh", text)
        root_path = Path(self.run_dir) / "script.sh"
        write_text_lf(root_path, text)
        return str(root_path)

    def write_inputs(self, files: dict[str, str]) -> None:
        """运行目录根的输入快照（plan.md / template.sh）：让运行目录自包含可回放。"""
        # 与 write_script / write_meta 保持一致的自我修复：不假定调用方先调过 init()。
        target = Path(self.run_dir)
        target.mkdir(parents=True, exist_ok=True)
        for name, content in files.items():
            write_text_lf(target / name, content)

    def write_attempt(self, round_no: int, files: dict[str, str]) -> None:
        target = Path(attempt_dir(self.run_dir, round_no))
        target.mkdir(parents=True, exist_ok=True)
        for name, content in files.items():
            write_text_lf(target / name, content)

    def write_meta(self, patch: dict[str, Any]) -> None:
        path = Path(self.run_dir) / "meta.json"
        # 与 write_script / write_attempt 保持一致的自我修复：三者都不该假定调用方先调过 init()。
        path.parent.mkdir(parents=True, exist_ok=True)
        current: dict[str, Any] = {}
        if path.exists():
            current = json.loads(path.read_text(encoding="utf-8"))
        current.update(patch)
        write_text_lf(path, json.dumps(current, ensure_ascii=False, indent=2) + "\n")
