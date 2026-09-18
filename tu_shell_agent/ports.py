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

    def chat(
        self,
        session_id: str,
        message: str,
        timeout_ms: int,
        on_delta: Callable[[str], None] | None = None,
        cancel: Any = None,
        system_preamble: str = "",
    ) -> str:
        """自由对话：**不带结构化输出 schema**，返回模型的纯文本回复。

        与 generate 的区别就在这一点上，而这个区别是安全属性的一部分：
        - `generate` 走 schema，产物必须填 `script`，且要过锚点契约校验，最终由引擎执行；
        - `chat` 只是说话。它产出的任何脚本都**不会自动执行** —— 要执行必须先进中栏、
          再走"改后重跑"（shellcheck + 人工确认）。
        """
        ...

    def abort(self, session_id: str) -> None: ...

    def dispose(self) -> None: ...


class ConfirmPort(Protocol):
    def confirm(self, round_no: int, script_path: str, script: str, trusted: bool) -> bool:
        """返回 False 表示用户拒绝执行本次脚本。"""
        ...


class RunStorePort(Protocol):
    run_dir: str

    def write_script(self, round_no: int, script: str) -> str: ...

    def write_inputs(self, files: dict[str, str]) -> None:
        """把本次运行的输入写到**运行目录根**（plan.md / template.sh），与 attempts/<n>/ 区分。"""
        ...

    def write_attempt(self, round_no: int, files: dict[str, str]) -> None: ...

    def write_meta(self, patch: dict[str, Any]) -> None: ...
