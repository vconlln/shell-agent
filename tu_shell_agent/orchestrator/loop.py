"""主状态机（规格 §6）。

只有这里知道流程；外部世界全部由 ports 注入，因此可在没有 opencode、
没有 Windows 的机器上完整单测。
"""

from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass, is_dataclass
from pathlib import Path
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


def _jsonable(value: Any) -> Any:
    """把 dataclass 递归转成**安全可序列化**的结构。

    不能直接塞 dataclass 对象进 meta.json（json.dumps 会 TypeError），也不能盲目
    `asdict` 后原样落盘：调用方传进来的 dataclass 可能含非 JSON 值。这里逐层过滤，
    只保留 json.dumps 真正认得的类型。
    """
    if is_dataclass(value) and not isinstance(value, type):
        return _jsonable(asdict(value))
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return str(value)


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
    try:
        detection = ports.toolchain.detect()
    except Exception as error:  # noqa: BLE001
        # detect 是最后一个裸调的端口。与 shellcheck/execute/start 三条路径保持同一不变量：
        # 端口抛异常不得穿出编排层，一律终止为 aborted_dependency（规格 §6、§13）。
        message = f"环境探测失败：{error}"
        emit(RunEvent("note", 0, {"message": message}))
        ports.store.write_attempt(0, {"detect-error.txt": message + "\n"})
        ports.store.write_meta({"outcome": "aborted_dependency", "rounds": 0})
        return LoopResult("aborted_dependency", 0)
    if detection.problems:
        emit(RunEvent("note", 0, {"message": "\n".join(detection.problems)}))
        return LoopResult("aborted_dependency", 0)

    # 规格 §10：运行目录要自包含可回放 —— 输入（方案全文、渲染后的骨架）必须落盘，
    # 否则事后无法判断"当时到底让它实现什么"。
    ports.store.write_inputs({"plan.md": input_.plan, "template.sh": skeleton})

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

    # 规格 §10：meta.json 要含「输入摘要、配置快照、自检结果、轮次结论、sessionId」。
    # 在 start() 成功之后统一包一层，避免 8 处 write_meta 各写一遍。
    def write_meta(patch: dict[str, Any]) -> None:
        ports.store.write_meta(
            {
                "runId": Path(input_.run_dir).name,
                "sessionId": session_id,
                "config": _jsonable(config),
                "detection": _jsonable(detection),
                **patch,
            }
        )

    for round_no in range(1, config.max_rounds + 1):
        if _cancelled(input_.cancel):
            # 与另两处取消路径（用户拒绝、执行取消）保持一致：终态要落盘，
            # 否则运行目录里没有 meta.json，Plan 2 的历史列表读不到"这次已被取消"。
            write_meta({"outcome": "cancelled", "rounds": round_no - 1})
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
            if _cancelled(input_.cancel):
                # 生成阶段（最长 generate_timeout_ms）是用户最可能按取消的地方。
                # 取消不是"生成失败"：白烧一轮契约失败会让用户看到 needs_human 之类的假象，
                # 所以必须先判取消，按取消终态落盘并返回（规格 §6 的 cancelled 分支）。
                emit(RunEvent("note", round_no, {"message": "已取消"}))
                write_meta({"outcome": "cancelled", "rounds": round_no - 1})
                return LoopResult("cancelled", round_no - 1, last_findings=last_findings)
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
            write_meta({"outcome": "aborted_dependency", "rounds": round_no})
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
            write_meta({"outcome": "cancelled", "rounds": round_no})
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
            write_meta({"outcome": "aborted_dependency", "rounds": round_no})
            return LoopResult(
                "aborted_dependency", round_no, script_path, last_findings, last_execute
            )
        last_execute = result
        ports.store.write_attempt(
            round_no,
            {
                "stdout.txt": result.stdout,
                "stderr.txt": result.stderr,
                # 必须用 json.dumps：手写 f-string 在 exit_code 为 None（取消路径）时会写出
                # `"exit_code": None` —— 那不是合法 JSON，Plan 2 回放时 json.loads 会炸。
                "execute.json": json.dumps(
                    {
                        "exit_code": result.exit_code,
                        "timed_out": result.timed_out,
                        "cancelled": result.cancelled,
                        "duration_ms": result.duration_ms,
                    },
                    ensure_ascii=False,
                )
                + "\n",
            },
        )
        emit(RunEvent("execute", round_no, {"result": result}))

        if result.exit_code == 0 and not result.timed_out and not result.cancelled:
            write_meta(
                {
                    "outcome": "succeeded",
                    "rounds": round_no,
                    "duration_ms": int((time.monotonic() - started) * 1000),
                }
            )
            emit(RunEvent("phase", round_no, {"phase": "settled"}))
            return LoopResult("succeeded", round_no, script_path, last_findings, result)

        if result.cancelled:
            write_meta({"outcome": "cancelled", "rounds": round_no})
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

    write_meta({"outcome": "needs_human", "rounds": config.max_rounds})
    emit(RunEvent("phase", config.max_rounds, {"phase": "settled"}))
    return LoopResult("needs_human", config.max_rounds, None, last_findings, last_execute)
