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
        *,
        detect: Callable[[], DetectionReport] | None = None,
    ) -> None:
        """`detect` 是探测钩子：引擎在每轮开头调 `ports.toolchain.detect()` 做环境预检，而
        **依赖集合随 agent 后端而变** —— 换成命令行后端之后 opencode 不再是依赖，硬编码
        `detect_all` 会让没装 opencode 的机器在预检处直接终止（"换了后端还是跑不起来"）。
        默认实现保持原样（三件套），由装配方按当前后端注入另一个实现。
        """
        self._bash_path = bash_path
        self._shellcheck_path = shellcheck_path
        self._overrides = dict(overrides or {})
        self._detect = detect

    def detect(self) -> DetectionReport:
        if self._detect is not None:
            return self._detect()
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
