"""引擎与外界的四个端口。orchestrator 只认这些 Protocol，不认具体实现。"""

from __future__ import annotations

from typing import Any, Callable, Protocol

from .types import (
    DetectionReport,
    ExecuteResult,
    GeneratedScript,
    ShellcheckFinding,
)


class ToolchainPort(Protocol):
    def detect(self) -> DetectionReport: ...

    def shellcheck(self, script_path: str) -> tuple[list[ShellcheckFinding], int, str]:
        """返回 (发现列表, shellcheck 退出码, 原始 json1 文本)。"""
        ...

    def execute(
        self,
        script_path: str,
        cwd: str,
        timeout_ms: int,
        cancel: Any = None,
        on_stdout: Callable[[str], None] | None = None,
        on_stderr: Callable[[str], None] | None = None,
    ) -> ExecuteResult: ...


class OpencodePort(Protocol):
    def start(self, run_dir: str, agent_name: str, model: str | None) -> str:
        """起 server、建会话，返回 session_id。"""
        ...

    def generate(
        self,
        session_id: str,
        message: str,
        schema: dict[str, Any],
        timeout_ms: int,
        on_delta: Callable[[str], None] | None = None,
        cancel: Any = None,
    ) -> GeneratedScript: ...

    def abort(self, session_id: str) -> None: ...

    def dispose(self) -> None: ...


class ConfirmPort(Protocol):
    def confirm(self, round_no: int, script_path: str, script: str, trusted: bool) -> bool:
        """返回 False 表示用户拒绝执行本次脚本。"""
        ...


class RunStorePort(Protocol):
    run_dir: str

    def write_script(self, round_no: int, script: str) -> str: ...

    def write_attempt(self, round_no: int, files: dict[str, str]) -> None: ...

    def write_meta(self, patch: dict[str, Any]) -> None: ...
