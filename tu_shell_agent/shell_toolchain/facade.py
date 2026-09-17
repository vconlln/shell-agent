"""把探测 / shellcheck / 执行组装成 ToolchainPort。"""

from __future__ import annotations

from typing import Any, Callable

from ..types import DetectionReport, ExecuteResult, ShellcheckFinding
from .detect import detect_all, system_deps
from .execute import run_script
from .shellcheck import run_shellcheck


class ShellToolchain:
    def __init__(
        self,
        bash_path: str,
        shellcheck_path: str,
        overrides: dict[str, str] | None = None,
    ) -> None:
        self._bash_path = bash_path
        self._shellcheck_path = shellcheck_path
        self._overrides = dict(overrides or {})

    def detect(self) -> DetectionReport:
        return detect_all(system_deps(self._overrides))

    def shellcheck(self, script_path: str) -> tuple[list[ShellcheckFinding], int, str]:
        return run_shellcheck(self._shellcheck_path, script_path)

    def execute(
        self,
        script_path: str,
        cwd: str,
        timeout_ms: int,
        cancel: Any = None,
        on_stdout: Callable[[str], None] | None = None,
        on_stderr: Callable[[str], None] | None = None,
    ) -> ExecuteResult:
        return run_script(
            self._bash_path,
            script_path,
            cwd,
            timeout_ms,
            cancel=cancel,
            on_stdout=on_stdout,
            on_stderr=on_stderr,
        )
