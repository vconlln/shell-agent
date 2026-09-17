"""全部共享类型。本模块是类型的唯一来源，其它模块不得重复定义这些结构。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

Severity = Literal["error", "warning", "info", "style"]

SEVERITY_RANK: dict[Severity, int] = {"error": 3, "warning": 2, "info": 1, "style": 0}


def blocks_run(level: Severity, blocking_level: Severity) -> bool:
    """这条 shellcheck 发现是否应当阻断本次运行（级别 >= 配置的阻断级别）。"""
    return SEVERITY_RANK[level] >= SEVERITY_RANK[blocking_level]


Stage = Literal["contract", "shellcheck", "execute"]
RunOutcome = Literal["succeeded", "needs_human", "cancelled", "aborted_dependency"]
ContractFailure = Literal["empty", "too_large", "has_crlf", "missing_anchor"]


@dataclass(frozen=True, slots=True)
class ShellcheckFinding:
    code: str
    line: int
    column: int
    level: Severity
    message: str


@dataclass(frozen=True, slots=True)
class ExecuteResult:
    exit_code: int | None
    signal: int | None
    timed_out: bool
    cancelled: bool
    duration_ms: int
    stdout: str
    stderr: str


@dataclass(frozen=True, slots=True)
class ContractResult:
    ok: bool
    reason: ContractFailure | None
    missing_anchors: tuple[str, ...]
    size_bytes: int


@dataclass(frozen=True, slots=True)
class GeneratedScript:
    script: str
    notes: str
    assumptions: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ContractEvidence:
    reason: ContractFailure
    missing_anchors: tuple[str, ...] = ()
    # message 用于「这一轮根本没产出脚本」（结构化输出失败/超时），此时 reason 记 empty
    message: str | None = None


@dataclass(frozen=True, slots=True)
class ExecuteEvidence:
    exit_code: int | None
    timed_out: bool
    stdout_tail: str
    stderr_tail: str
    duration_ms: int


@dataclass(frozen=True, slots=True)
class FailureEvidence:
    round: int
    stage: Stage
    contract: ContractEvidence | None = None
    shellcheck: tuple[ShellcheckFinding, ...] = ()
    execute: ExecuteEvidence | None = None


@dataclass(frozen=True, slots=True)
class DetectedTool:
    path: str
    version: str


@dataclass(frozen=True, slots=True)
class DetectionReport:
    opencode: DetectedTool | None
    bash: DetectedTool | None
    shellcheck: DetectedTool | None
    problems: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class RunConfig:
    run_root: str
    max_rounds: int = 3
    generate_timeout_ms: int = 300_000
    execute_timeout_ms: int = 120_000
    blocking_level: Severity = "warning"
    bash_path: str | None = None
    shellcheck_path: str | None = None
    opencode_path: str | None = None
    model: str | None = None


@dataclass(frozen=True, slots=True)
class RunEvent:
    """给 UI 的事件。type 取值见 loop.py 的 emit 调用点。"""

    type: str
    round: int = 0
    payload: dict[str, Any] = field(default_factory=dict)
