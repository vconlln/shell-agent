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
    # 上一轮那份**脚本本身**（修复要在它基础上改）。用户报的问题正是缺了它：
    # 修复消息里只有失败证据 + 模板骨架，模型手里没有"我刚才写的那一版"，
    # 于是它照着骨架重写一遍 —— 表现就是"一出错就退回模板的 shell 代码"。
    # 生成阶段就失败的轮次没有脚本，留空（那时确实只能从骨架来）。
    script: str = ""


@dataclass(frozen=True, slots=True)
class DetectedTool:
    path: str
    version: str
    # 非空 = 这个文件找到了、但**起不来**（附一句能看懂的原因）。典型场景：把仓库里随附的
    # Linux 版 shellcheck 填进了 Windows 的「组件路径」，启动时得到
    # `[WinError 193] %1 不是有效的 Win32 应用程序`。这时**不能**当成"未找到"去报
    # （用户会去重装一个本来就有的东西），也不能当成可用（运行时才炸成一句原始 OSError）。
    error: str = ""


@dataclass(frozen=True, slots=True)
class DetectionReport:
    opencode: DetectedTool | None
    bash: DetectedTool | None
    shellcheck: DetectedTool | None
    problems: tuple[str, ...]
    # 提示（不是故障）：三件套都在、但某件事会让后面的运行大概率失败，而它又**不能确定**
    # 一定失败。例如 opencode 里没保存凭据 —— 用户完全可能用环境变量提供 API key，
    # 那就一切正常。塞进 problems 会让 CLI 直接拒绝运行（假故障），所以单独一栏。
    warnings: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class RunConfig:
    run_root: str
    max_rounds: int = 3
    generate_timeout_ms: int = 300_000
    execute_timeout_ms: int = 120_000
    blocking_level: Severity = "info"  # 实测 SC2086 就是 info 级，用 warning 会让它"只展示不修"
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
