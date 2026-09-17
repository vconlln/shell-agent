"""主状态机（规格 §6）。

只有这里知道流程；外部世界全部由 ports 注入，因此可在没有 opencode、
没有 Windows 的机器上完整单测。
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Callable

from ..ports import ConfirmPort, OpencodePort, RunStorePort, ToolchainPort
from ..template_store.render import PlaceholderSpec, render_template
from ..types import (
    ContractEvidence,
    ExecuteEvidence,
    ExecuteResult,
    FailureEvidence,
    RunConfig,
    RunEvent,
    RunOutcome,
    ShellcheckFinding,
    blocks_run,
)
from .contract import check_contract, extract_anchors, normalize_script
from .prompt import OUTPUT_SCHEMA, build_first_message, build_repair_message

TAIL_LINES = 40


@dataclass(frozen=True, slots=True)
class TemplateSpec:
    id: str
    body: str
    anchors: tuple[str, ...] = ()
    trusted: bool = False
    placeholders: tuple[PlaceholderSpec, ...] = ()


@dataclass(frozen=True, slots=True)
class LoopPorts:
    opencode: OpencodePort
    toolchain: ToolchainPort
    confirm: ConfirmPort
    store: RunStorePort
    emit: Callable[[RunEvent], None]


@dataclass(frozen=True, slots=True)
class LoopInput:
    plan: str
    template: TemplateSpec
    values: dict[str, str]
    run_dir: str
    config: RunConfig
    ports: LoopPorts
    agent_name: str = "tu-shell-writer"
    cancel: Any = None


@dataclass(frozen=True, slots=True)
class LoopResult:
    outcome: RunOutcome
    rounds: int
    script_path: str | None = None
    last_findings: tuple[ShellcheckFinding, ...] = ()
    last_execute: ExecuteResult | None = None


def _tail(text: str) -> str:
    return "\n".join(text.split("\n")[-TAIL_LINES:])


def _cancelled(cancel: Any) -> bool:
    is_set = getattr(cancel, "is_set", None)
    return bool(callable(is_set) and is_set())


def render_findings(findings: list[ShellcheckFinding] | tuple[ShellcheckFinding, ...]) -> str:
    return (
        "\n".join(f"{f.code} {f.line}:{f.column} {f.level} {f.message}" for f in findings) + "\n"
    )


def run_loop(input_: LoopInput) -> LoopResult:
    ports = input_.ports
    config = input_.config
    emit = ports.emit

    skeleton = render_template(
        input_.template.body, list(input_.template.placeholders), input_.values
    )
    anchors = (
        input_.template.anchors if input_.template.anchors else extract_anchors(skeleton)
    )

    emit(RunEvent("phase", 0, {"phase": "precheck"}))
    detection = ports.toolchain.detect()
    if detection.problems:
        emit(RunEvent("note", 0, {"message": "\n".join(detection.problems)}))
        return LoopResult("aborted_dependency", 0)

    try:
        session_id = ports.opencode.start(input_.run_dir, input_.agent_name, config.model)
    except Exception as error:  # noqa: BLE001 - 启动失败要变成终止态，不是异常
        message = f"opencode 启动失败：{error}"
        emit(RunEvent("note", 0, {"message": message}))
        ports.store.write_attempt(0, {"start-error.txt": message + "\n"})
        ports.store.write_meta({"outcome": "aborted_dependency", "rounds": 0})
        return LoopResult("aborted_dependency", 0)

    evidence: FailureEvidence | None = None
    last_findings: tuple[ShellcheckFinding, ...] = ()
    last_execute: ExecuteResult | None = None

    for round_no in range(1, config.max_rounds + 1):
        if _cancelled(input_.cancel):
            return LoopResult("cancelled", round_no - 1, last_findings=last_findings)

        started = time.monotonic()
        emit(RunEvent("phase", round_no, {"phase": "generating" if round_no == 1 else "repairing"}))

        message = (
            build_first_message(
                skeleton=skeleton, anchors=anchors, plan=input_.plan, run_dir=input_.run_dir
            )
            if evidence is None
            else build_repair_message(evidence=evidence, anchors=anchors, skeleton=skeleton)
        )

        try:
            generated = ports.opencode.generate(
                session_id,
                message,
                OUTPUT_SCHEMA,
                config.generate_timeout_ms,
                on_delta=lambda text, r=round_no: emit(RunEvent("assistant_delta", r, {"text": text})),
                cancel=input_.cancel,
            )
        except Exception as error:  # noqa: BLE001 - 结构化输出失败计一次契约失败
            failure = str(error)
            evidence = FailureEvidence(
                round=round_no,
                stage="contract",
                contract=ContractEvidence(reason="empty", message=failure),
            )
            ports.store.write_attempt(round_no, {"generation-error.txt": failure + "\n"})
            emit(RunEvent("note", round_no, {"message": f"第 {round_no} 轮生成失败：{failure}"}))
            continue

        script = normalize_script(generated.script)
        emit(RunEvent("script", round_no, {"script": script}))
        script_path = ports.store.write_script(round_no, script)
        ports.store.write_attempt(
            round_no,
            {
                "notes.md": f"{generated.notes}\n\n## 假设\n"
                + "\n".join(f"- {item}" for item in generated.assumptions)
                + "\n"
            },
        )

        emit(RunEvent("phase", round_no, {"phase": "checking"}))
        contract = check_contract(script, anchors)
        if not contract.ok:
            evidence = FailureEvidence(
                round=round_no,
                stage="contract",
                contract=ContractEvidence(
                    reason=contract.reason or "empty",
                    missing_anchors=contract.missing_anchors,
                ),
            )
            ports.store.write_attempt(
                round_no, {"contract.json": f"{contract}\n"}
            )
            emit(RunEvent("note", round_no, {"message": f"第 {round_no} 轮契约失败：{contract.reason}"}))
            continue

        try:
            findings, _exit_code, raw = ports.toolchain.shellcheck(script_path)
        except Exception as error:  # noqa: BLE001
            # shellcheck 自身故障（文件读不了、参数错）不是脚本的问题，也不该带崩编排：
            # 按规格 §13 直接终止为依赖错误，不进入修复循环。
            # 注意实证事实：退出码 2 时 shellcheck 的 stdout 仍是合法空 JSON，
            # 所以判空必须靠异常，不能靠 stdout。
            message = f"shellcheck 调用失败：{error}"
            emit(RunEvent("note", round_no, {"message": message}))
            ports.store.write_attempt(round_no, {"shellcheck-error.txt": message + "\n"})
            ports.store.write_meta({"outcome": "aborted_dependency", "rounds": round_no})
            return LoopResult(
                "aborted_dependency", round_no, script_path, last_findings, last_execute
            )
        last_findings = tuple(findings)
        ports.store.write_attempt(
            round_no,
            {"shellcheck.json": raw, "shellcheck.txt": render_findings(findings)},
        )
        emit(RunEvent("shellcheck", round_no, {"findings": last_findings}))

        blocking = [f for f in findings if blocks_run(f.level, config.blocking_level)]
        if blocking:
            evidence = FailureEvidence(
                round=round_no, stage="shellcheck", shellcheck=tuple(findings)
            )
            continue

        emit(RunEvent("phase", round_no, {"phase": "confirming"}))
        approved = input_.template.trusted or ports.confirm.confirm(
            round_no, script_path, script, input_.template.trusted
        )
        if not approved:
            ports.store.write_meta({"outcome": "cancelled", "rounds": round_no})
            return LoopResult(
                "cancelled", round_no, script_path, last_findings, last_execute
            )

        emit(RunEvent("phase", round_no, {"phase": "executing"}))
        try:
            result = ports.toolchain.execute(
                script_path,
                input_.run_dir,
                config.execute_timeout_ms,
                cancel=input_.cancel,
            )
        except Exception as error:  # noqa: BLE001
            # 依赖中途损坏（例如 bash 在探测之后被移走）：规格 §6 的 aborted_dependency 正是这种情形，
            # 不能让异常穿出编排层。
            message = f"执行失败（依赖问题）：{error}"
            emit(RunEvent("note", round_no, {"message": message}))
            ports.store.write_attempt(round_no, {"execute-error.txt": message + "\n"})
            ports.store.write_meta({"outcome": "aborted_dependency", "rounds": round_no})
            return LoopResult(
                "aborted_dependency", round_no, script_path, last_findings, last_execute
            )
        last_execute = result
        ports.store.write_attempt(
            round_no,
            {
                "stdout.txt": result.stdout,
                "stderr.txt": result.stderr,
                "execute.json": (
                    f'{{"exit_code": {result.exit_code}, "timed_out": {str(result.timed_out).lower()}, '
                    f'"cancelled": {str(result.cancelled).lower()}, "duration_ms": {result.duration_ms}}}\n'
                ),
            },
        )
        emit(RunEvent("execute", round_no, {"result": result}))

        if result.exit_code == 0 and not result.timed_out and not result.cancelled:
            ports.store.write_meta(
                {
                    "outcome": "succeeded",
                    "rounds": round_no,
                    "duration_ms": int((time.monotonic() - started) * 1000),
                }
            )
            emit(RunEvent("phase", round_no, {"phase": "settled"}))
            return LoopResult("succeeded", round_no, script_path, last_findings, result)

        if result.cancelled:
            ports.store.write_meta({"outcome": "cancelled", "rounds": round_no})
            return LoopResult("cancelled", round_no, script_path, last_findings, result)

        evidence = FailureEvidence(
            round=round_no,
            stage="execute",
            execute=ExecuteEvidence(
                exit_code=result.exit_code,
                timed_out=result.timed_out,
                stdout_tail=_tail(result.stdout),
                stderr_tail=_tail(result.stderr),
                duration_ms=result.duration_ms,
            ),
        )

    ports.store.write_meta({"outcome": "needs_human", "rounds": config.max_rounds})
    emit(RunEvent("phase", config.max_rounds, {"phase": "settled"}))
    return LoopResult("needs_human", config.max_rounds, None, last_findings, last_execute)
