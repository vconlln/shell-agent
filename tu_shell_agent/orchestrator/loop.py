"""主状态机（规格 §6）。

只有这里知道流程；外部世界全部由 ports 注入，因此可在没有 opencode、
没有 Windows 的机器上完整单测。

三个入口共用同一批辅助，各自只表达「差在哪」：`_precheck`（环境预检 + 输入落盘）、
`_shellcheck_script` / `_confirm_and_execute` / `_judge`（一轮里的校验、执行与判决）、
`_make_meta_writer`（meta 记账）、`_drive_loop`（轮次循环本体，前两个入口共用）。

- `run_loop`           —— 从第 1 轮开始跑完整循环（生成 → 校验 → 执行 → 修复）；
- `resume_repair`      —— 在**既有** opencode 会话上从第 n 轮继续修（规格 §6）；
- `verify_and_execute` —— 对用户手工改过的脚本只重跑「校验 + 执行」（规格 §12），不生成、不烧轮次。

后两个入口存在的意义就是**不让界面自己拼装编排步骤**：界面只挑入口、给参数。
"""

from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass, is_dataclass
from pathlib import Path
from typing import Any, Callable

from ..ports import ConfirmPort, OpencodePort, RunStorePort, ToolchainPort
from ..template_store.render import PlaceholderSpec, render_template, to_lf
from ..types import (
    ContractEvidence,
    ContractResult,
    DetectionReport,
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


class _ShellcheckError(Exception):
    """shellcheck 自身故障（文件读不了、参数错）。

    单独一个类型、并且**带出本轮脚本路径**：它在 `_check_script` 内部抛出、由调用方兜底，
    而兜底既要落 `shellcheck-error.txt`，也要把 `LoopResult.script_path` 如实填上（脚本
    已经写盘了，界面要用这个路径做「手工改完再重跑」）。异常文本原样保留，调用方拼出来的
    提示与重构前逐字一致。
    """

    def __init__(self, message: str, script_path: str) -> None:
        super().__init__(message)
        self.script_path = script_path


class _ExecuteError(Exception):
    """执行端口的故障（例如 bash 在探测之后被移走）。

    与 `_ShellcheckError` 同理：`_confirm_and_execute` 里**只有 execute 这一句**需要兜底 ——
    确认对话框自身的异常不该被算成「执行失败」（重构前 confirm 就没有兜底），单独一个类型
    把兜底范围锁死在 execute 上。
    """


def _precheck(
    *, ports: LoopPorts, run_dir: str, plan: str, skeleton: str
) -> tuple[DetectionReport | None, LoopResult | None]:
    """环境预检 + 输入落盘。返回 (探测结果, 要直接终止的结果)。

    第二个元素非 None 时调用方原样返回它。detect 是唯一裸调的端口，与
    shellcheck/execute/start 三条路径保持同一不变量：端口抛异常不得穿出编排层，
    一律终止为 aborted_dependency（规格 §6、§13）。
    """
    emit = ports.emit
    emit(RunEvent("phase", 0, {"phase": "precheck"}))
    try:
        detection = ports.toolchain.detect()
    except Exception as error:  # noqa: BLE001
        message = f"环境探测失败：{error}"
        emit(RunEvent("note", 0, {"message": message}))
        ports.store.write_attempt(0, {"detect-error.txt": message + "\n"})
        ports.store.write_meta({"outcome": "aborted_dependency", "rounds": 0})
        return None, LoopResult("aborted_dependency", 0)
    if detection.problems:
        # 与上面 detect 抛异常那条分支一样落终态：否则运行目录里会留下"有目录、无 meta.json"
        # 的运行，Plan 2 的历史列表读到会缺结论。
        emit(RunEvent("note", 0, {"message": "\n".join(detection.problems)}))
        ports.store.write_meta({"outcome": "aborted_dependency", "rounds": 0})
        return None, LoopResult("aborted_dependency", 0)

    # 规格 §10：运行目录要自包含可回放 —— 输入（方案全文、渲染后的骨架）必须落盘，
    # 否则事后无法判断"当时到底让它实现什么"。
    ports.store.write_inputs({"plan.md": plan, "template.sh": skeleton})
    return detection, None


def _make_meta_writer(
    ports: LoopPorts, run_dir: str, extras: dict[str, Any]
) -> Callable[[dict[str, Any]], None]:
    """meta.json 的统一记账入口：`runId` + 本次运行的固定快照（`extras`）+ 每次调用的 patch。

    「runId 由 run_dir 的 basename 推出来」「配置快照要先过 `_jsonable`」这些规矩，三个入口
    各写一遍迟早漂移，所以收敛成一个写入器；`verify_and_execute` 没有 session 与探测结果，
    就少给两个 key，其余记账方式完全相同。
    """
    base: dict[str, Any] = {"runId": Path(run_dir).name, **extras}

    def write_meta(patch: dict[str, Any]) -> None:
        ports.store.write_meta({**base, **patch})

    return write_meta


def _shellcheck_script(
    *, round_no: int, script_path: str, ports: LoopPorts, emit: Callable[[RunEvent], None]
) -> tuple[ShellcheckFinding, ...]:
    """shellcheck + 证据落盘 + emit。故障包成 `_ShellcheckError` 抛给调用方兜底。

    注意实证事实：退出码 2 时 shellcheck 的 stdout 仍是合法空 JSON，
    所以判空必须靠异常，不能靠 stdout。
    """
    try:
        findings, _exit_code, raw = ports.toolchain.shellcheck(script_path)
    except Exception as error:  # noqa: BLE001
        raise _ShellcheckError(str(error), script_path) from error

    findings_tuple = tuple(findings)
    ports.store.write_attempt(
        round_no,
        {"shellcheck.json": raw, "shellcheck.txt": render_findings(findings)},
    )
    emit(RunEvent("shellcheck", round_no, {"findings": findings_tuple}))
    return findings_tuple


def _check_script(
    *,
    round_no: int,
    script: str,
    anchors: tuple[str, ...],
    notes: str,
    ports: LoopPorts,
    emit: Callable[[RunEvent], None],
) -> tuple[str, ContractResult | None, tuple[ShellcheckFinding, ...]]:
    """写盘 → 契约校验 → shellcheck。返回 (script_path, 契约失败?, 本轮全部发现)。

    契约失败时第二个元素非 None 且第三个为空；shellcheck 自身故障会抛
    （`_ShellcheckError`，由调用方兜底成 aborted_dependency）。

    第三个元素是**本轮全部发现**，不做阻断性过滤：调用方要拿它整份灌进 FailureEvidence
    回灌给模型，也要用它算 `last_findings`，过滤掉就等于把该修的东西藏了 —— 阻断性判定是
    调用方按 `config.blocking_level` 的职责。

    `script` 由调用方 normalize 过（规范化必须在**调用方**做：确认对话框拿到的必须是规范化
    后的脚本，而它的返回值带不出规范化结果）；`notes` 是本轮 notes.md 的内容。
    """
    emit(RunEvent("script", round_no, {"script": script}))
    script_path = ports.store.write_script(round_no, script)
    ports.store.write_attempt(round_no, {"notes.md": notes})

    emit(RunEvent("phase", round_no, {"phase": "checking"}))
    contract = check_contract(script, anchors)
    if not contract.ok:
        return script_path, contract, ()

    return script_path, None, _shellcheck_script(
        round_no=round_no, script_path=script_path, ports=ports, emit=emit
    )


def _confirm_and_execute(
    *,
    round_no: int,
    script_path: str,
    script: str,
    trusted: bool,
    run_dir: str,
    config: RunConfig,
    ports: LoopPorts,
    cancel: Any,
    emit: Callable[[RunEvent], None],
) -> tuple[bool, ExecuteResult | None]:
    """确认 → 执行。返回 (是否获批, 执行结果或 None)。

    用户拒绝时第二个元素为 None，终态由调用方落盘；execute 故障抛 `_ExecuteError`，
    同样由调用方兜底成 aborted_dependency。
    """
    emit(RunEvent("phase", round_no, {"phase": "confirming"}))
    approved = trusted or ports.confirm.confirm(round_no, script_path, script, trusted)
    if not approved:
        return False, None

    emit(RunEvent("phase", round_no, {"phase": "executing"}))
    try:
        result = ports.toolchain.execute(
            script_path, run_dir, config.execute_timeout_ms, cancel=cancel
        )
    except Exception as error:  # noqa: BLE001
        raise _ExecuteError(str(error)) from error

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
    return True, result


def _judge(result: ExecuteResult, blocking_findings: tuple[ShellcheckFinding, ...]) -> str:
    """只判，不落盘。返回 `succeeded` / `cancelled` / `repair`（还要再修一轮）。

    - 退出码 0 且未超时未取消 → `succeeded`；
    - 执行被取消 → `cancelled`；
    - 其余（非零退出码、超时被杀）→ `repair`：把退出码与 stderr 回灌给模型再修一轮。

    `blocking_findings` 是本轮的阻断性发现，非空一律判 `repair`。两个调用点都在调用它之前
    就按它短路了（`_drive_loop` 不执行、`verify_and_execute` 直接 needs_human），所以实际
    进来时它必然是空的；做成入参是为了让这条判定不依赖调用顺序 —— 任何顺序下不合格的脚本
    都不会被判成成功。
    """
    if blocking_findings:
        return "repair"
    if result.exit_code == 0 and not result.timed_out and not result.cancelled:
        return "succeeded"
    if result.cancelled:
        return "cancelled"
    return "repair"


def _drive_loop(
    *,
    ports: LoopPorts,
    config: RunConfig,
    session_id: str,
    skeleton: str,
    anchors: tuple[str, ...],
    plan: str,
    run_dir: str,
    template: TemplateSpec,
    cancel: Any,
    first_round: int,
    evidence: FailureEvidence | None,
    write_meta: Callable[[dict[str, Any]], None],
) -> LoopResult:
    """修复循环本体，`run_loop` 与 `resume_repair` 共用。

    两者只有两处差量，都由入参表达：从第几轮开始（`first_round`），以及首轮是否已经握着
    失败证据（`evidence is None` → 发首轮消息，否则发修复消息）。其余（取消、生成失败、
    契约失败、shellcheck 故障、阻断、拒绝、执行故障、终局落盘）逐字共用，不许各写一份。
    """
    emit = ports.emit
    last_findings: tuple[ShellcheckFinding, ...] = ()
    last_execute: ExecuteResult | None = None

    for round_no in range(first_round, config.max_rounds + 1):
        if _cancelled(cancel):
            # 与另两处取消路径（用户拒绝、执行取消）保持一致：终态要落盘，
            # 否则运行目录里没有 meta.json，Plan 2 的历史列表读不到"这次已被取消"。
            write_meta({"outcome": "cancelled", "rounds": round_no - 1})
            return LoopResult("cancelled", round_no - 1, last_findings=last_findings)

        started = time.monotonic()
        # 阶段名沿用规格 §6 的词汇：第 1 轮是"生成"，其余轮是"修复"。续跑时起始轮号 > 1，
        # 本来就是在修，所以判的是轮次号而不是"循环的第几次"。
        emit(
            RunEvent(
                "phase", round_no, {"phase": "generating" if round_no == 1 else "repairing"}
            )
        )

        message = (
            build_first_message(
                skeleton=skeleton, anchors=anchors, plan=plan, run_dir=run_dir
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
                on_delta=lambda text, r=round_no: emit(
                    RunEvent("assistant_delta", r, {"text": text})
                ),
                cancel=cancel,
            )
        except Exception as error:  # noqa: BLE001 - 结构化输出失败计一次契约失败
            failure = str(error)
            if _cancelled(cancel):
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

        # normalize 留在循环里（与重构前同一位置）：确认对话框拿到的必须是**规范化后**的
        # 脚本，而规范化结果带不进 _check_script 的返回值，所以不能藏进那个辅助里。
        script = normalize_script(generated.script)
        try:
            script_path, contract, findings = _check_script(
                round_no=round_no,
                script=script,
                anchors=anchors,
                notes=(
                    f"{generated.notes}\n\n## 假设\n"
                    + "\n".join(f"- {item}" for item in generated.assumptions)
                    + "\n"
                ),
                ports=ports,
                emit=emit,
            )
        except _ShellcheckError as error:
            # shellcheck 自身故障（文件读不了、参数错）不是脚本的问题，也不该带崩编排：
            # 按规格 §13 直接终止为依赖错误，不进入修复循环。
            note = f"shellcheck 调用失败：{error}"
            emit(RunEvent("note", round_no, {"message": note}))
            ports.store.write_attempt(round_no, {"shellcheck-error.txt": note + "\n"})
            write_meta({"outcome": "aborted_dependency", "rounds": round_no})
            return LoopResult(
                "aborted_dependency", round_no, error.script_path, last_findings, last_execute
            )

        if contract is not None:
            evidence = FailureEvidence(
                round=round_no,
                stage="contract",
                contract=ContractEvidence(
                    reason=contract.reason or "empty",
                    missing_anchors=contract.missing_anchors,
                ),
            )
            ports.store.write_attempt(round_no, {"contract.json": f"{contract}\n"})
            emit(
                RunEvent(
                    "note", round_no, {"message": f"第 {round_no} 轮契约失败：{contract.reason}"}
                )
            )
            continue

        last_findings = findings
        blocking = tuple(f for f in findings if blocks_run(f.level, config.blocking_level))
        if blocking:
            evidence = FailureEvidence(round=round_no, stage="shellcheck", shellcheck=findings)
            continue

        try:
            approved, result = _confirm_and_execute(
                round_no=round_no,
                script_path=script_path,
                script=script,
                trusted=template.trusted,
                run_dir=run_dir,
                config=config,
                ports=ports,
                cancel=cancel,
                emit=emit,
            )
        except _ExecuteError as error:
            # 依赖中途损坏（例如 bash 在探测之后被移走）：规格 §6 的 aborted_dependency
            # 正是这种情形，不能让异常穿出编排层。
            note = f"执行失败（依赖问题）：{error}"
            emit(RunEvent("note", round_no, {"message": note}))
            ports.store.write_attempt(round_no, {"execute-error.txt": note + "\n"})
            write_meta({"outcome": "aborted_dependency", "rounds": round_no})
            return LoopResult(
                "aborted_dependency", round_no, script_path, last_findings, last_execute
            )

        if not approved:
            write_meta({"outcome": "cancelled", "rounds": round_no})
            return LoopResult("cancelled", round_no, script_path, last_findings, last_execute)

        last_execute = result
        verdict = _judge(result, blocking)
        if verdict == "succeeded":
            write_meta(
                {
                    "outcome": "succeeded",
                    "rounds": round_no,
                    "duration_ms": int((time.monotonic() - started) * 1000),
                }
            )
            emit(RunEvent("phase", round_no, {"phase": "settled"}))
            return LoopResult("succeeded", round_no, script_path, last_findings, result)

        if verdict == "cancelled":
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


def run_loop(input_: LoopInput) -> LoopResult:
    """从第 1 轮开始跑完整循环。"""
    ports = input_.ports
    config = input_.config
    emit = ports.emit

    skeleton = render_template(
        input_.template.body, list(input_.template.placeholders), input_.values
    )
    anchors = (
        input_.template.anchors if input_.template.anchors else extract_anchors(skeleton)
    )

    detection, aborted = _precheck(
        ports=ports, run_dir=input_.run_dir, plan=input_.plan, skeleton=skeleton
    )
    if aborted is not None:
        return aborted

    try:
        session_id = ports.opencode.start(input_.run_dir, input_.agent_name, config.model)
    except Exception as error:  # noqa: BLE001 - 启动失败要变成终止态，不是异常
        message = f"opencode 启动失败：{error}"
        emit(RunEvent("note", 0, {"message": message}))
        ports.store.write_attempt(0, {"start-error.txt": message + "\n"})
        ports.store.write_meta({"outcome": "aborted_dependency", "rounds": 0})
        return LoopResult("aborted_dependency", 0)

    # 规格 §10：meta.json 要含「输入摘要、配置快照、自检结果、轮次结论、sessionId」。
    # 在 start() 成功之后统一包一层，避免各处的 write_meta 各写一遍。
    write_meta = _make_meta_writer(
        ports,
        input_.run_dir,
        {
            "sessionId": session_id,
            "config": _jsonable(config),
            "detection": _jsonable(detection),
        },
    )
    return _drive_loop(
        ports=ports,
        config=config,
        session_id=session_id,
        skeleton=skeleton,
        anchors=anchors,
        plan=input_.plan,
        run_dir=input_.run_dir,
        template=input_.template,
        cancel=input_.cancel,
        first_round=1,
        evidence=None,
        write_meta=write_meta,
    )


@dataclass(frozen=True, slots=True)
class VerifyInput:
    """手工改过脚本后，只重跑"校验 + 执行"（不生成、不烧轮次）。"""

    script_path: str
    run_dir: str
    round_no: int
    config: RunConfig
    ports: LoopPorts
    trusted: bool = False
    cancel: Any = None


def verify_and_execute(input_: VerifyInput) -> LoopResult:
    """对已存在的脚本跑 shellcheck → 确认 → 执行。

    - shellcheck 有阻断性发现 → `needs_human`（把发现放进 last_findings，不执行、不生成）；
    - 用户拒绝 → `cancelled`；
    - 执行完成 → `succeeded` / `needs_human`（按退出码与 timed_out/cancelled 判定，与 run_loop 同规则）。

    脚本**不重新生成、也不做契约校验**，但入口处会做一次**换行归一化**：规格 §11 要求脚本
    不得含 `\r`（Git Bash 会报错），run_loop 靠写盘时 `to_lf` 保证这一点，而这里校验的是用户
    手改过的既有文件，所以要读进来归一化一次、必要时就地写回（归一化发生了就落
    `normalized.txt` 留证）。异常兜底与 run_loop 同一条规矩：shellcheck / execute 抛异常一律
    落 `*-error.txt` 并终止为 `aborted_dependency`，不穿出编排层。
    """
    ports = input_.ports
    config = input_.config
    emit = ports.emit
    round_no = input_.round_no
    script_path = input_.script_path

    write_meta = _make_meta_writer(ports, input_.run_dir, {"config": _jsonable(config)})

    try:
        path = Path(script_path)
        # 必须走字节读取：`Path.read_text()` 是通用换行模式，会把 \r\n 悄悄读成 \n，那样下面
        # 的 to_lf 永远看不出 CRLF（归一化会变成死代码），而磁盘上那份文件其实仍是 CRLF，
        # shellcheck 与 bash 照样报 \r 错。
        raw = path.read_bytes().decode("utf-8", errors="replace")
        script = raw
        # 规格 §11：脚本文件不得含 \r（Git Bash 会报 \r 错）。run_loop 靠写盘时 to_lf 保证，
        # 而这里校验的是用户手工改过的**既有文件**，所以要在入口补一次 —— 否则 Windows 上
        # 编辑器存出来的 CRLF 会变成"脚本莫名失败"。
        normalized = to_lf(raw)
        if normalized != raw:
            if "\ufffd" in raw:
                # errors="replace" 解出替换字符，说明这份文件不是 UTF-8（例如记事本存成 GBK）：
                # 回写等于把用户的手工修改永久损坏，所以只提示、不动文件，让 shellcheck 照实报。
                emit(
                    RunEvent(
                        "note",
                        round_no,
                        {"message": "脚本不是 UTF-8，跳过换行归一化（未改动文件）"},
                    )
                )
            else:
                # 同理必须走字节写入：`Path.write_text()` 在 Windows 上会把 \n 按 os.linesep
                # 又翻回 \r\n，白归一化一场（已实测 newline=None 的这个翻译行为）。
                path.write_bytes(normalized.encode("utf-8"))
                script = normalized
                ports.store.write_attempt(
                    round_no,
                    {
                        "normalized.txt": (
                            f"脚本含 {raw.count(chr(13))} 个 \\r，已就地归一化为 LF"
                            "（规格 §11：脚本不得含 \\r，否则 Git Bash 报错）。\n"
                        )
                    },
                )
    except OSError as error:
        # 读不到/写不回用户改过的脚本 = 校验阶段的外部依赖故障，与 shellcheck 自身故障落在同一处：
        # *-error.txt + aborted_dependency，界面据此提示"这份脚本读不到"。
        note = f"读取或归一化脚本失败：{error}"
        emit(RunEvent("note", round_no, {"message": note}))
        ports.store.write_attempt(round_no, {"shellcheck-error.txt": note + "\n"})
        write_meta({"outcome": "aborted_dependency", "rounds": round_no})
        return LoopResult("aborted_dependency", round_no, script_path)

    emit(RunEvent("phase", round_no, {"phase": "checking"}))
    try:
        findings = _shellcheck_script(
            round_no=round_no, script_path=script_path, ports=ports, emit=emit
        )
    except _ShellcheckError as error:
        note = f"shellcheck 调用失败：{error}"
        emit(RunEvent("note", round_no, {"message": note}))
        ports.store.write_attempt(round_no, {"shellcheck-error.txt": note + "\n"})
        write_meta({"outcome": "aborted_dependency", "rounds": round_no})
        return LoopResult("aborted_dependency", round_no, script_path)

    blocking = tuple(f for f in findings if blocks_run(f.level, config.blocking_level))
    if blocking:
        # 手工改过的脚本仍然不合格 → 不执行、不生成，如实报告（由界面决定是否回灌给模型）。
        emit(
            RunEvent(
                "note", round_no, {"message": f"仍有 {len(blocking)} 条阻断性发现，未执行"}
            )
        )
        write_meta({"outcome": "needs_human", "rounds": round_no})
        emit(RunEvent("phase", round_no, {"phase": "settled"}))
        return LoopResult("needs_human", round_no, script_path, findings, None)

    try:
        approved, result = _confirm_and_execute(
            round_no=round_no,
            script_path=script_path,
            script=script,
            trusted=input_.trusted,
            run_dir=input_.run_dir,
            config=config,
            ports=ports,
            cancel=input_.cancel,
            emit=emit,
        )
    except _ExecuteError as error:
        note = f"执行失败（依赖问题）：{error}"
        emit(RunEvent("note", round_no, {"message": note}))
        ports.store.write_attempt(round_no, {"execute-error.txt": note + "\n"})
        write_meta({"outcome": "aborted_dependency", "rounds": round_no})
        return LoopResult("aborted_dependency", round_no, script_path, findings, None)

    if not approved:
        write_meta({"outcome": "cancelled", "rounds": round_no})
        return LoopResult("cancelled", round_no, script_path, findings, None)

    verdict = _judge(result, blocking)
    if verdict == "succeeded":
        write_meta({"outcome": "succeeded", "rounds": round_no})
        emit(RunEvent("phase", round_no, {"phase": "settled"}))
        return LoopResult("succeeded", round_no, script_path, findings, result)

    if verdict == "cancelled":
        write_meta({"outcome": "cancelled", "rounds": round_no})
        return LoopResult("cancelled", round_no, script_path, findings, result)

    # "repair"：这个入口没有生成步骤，修不了 → 交人（把发现与执行结果如实带回去）。
    write_meta({"outcome": "needs_human", "rounds": round_no})
    emit(RunEvent("phase", round_no, {"phase": "settled"}))
    return LoopResult("needs_human", round_no, script_path, findings, result)


@dataclass(frozen=True, slots=True)
class ResumeInput:
    """在**既有 opencode 会话**上从第 n 轮继续（不重开会话、不重发首轮消息）。"""

    plan: str
    template: TemplateSpec
    values: dict[str, str]
    run_dir: str
    session_id: str
    start_round: int
    config: RunConfig
    ports: LoopPorts
    evidence: FailureEvidence | None = None
    # 本入口不调 start()，agent_name 用不到；保留字段是为了与 LoopInput 的装配参数对齐，
    # 让调用方（界面）在两个入口间换用时不必改参数表。
    agent_name: str = "tu-shell-writer"
    cancel: Any = None


def _resume_evidence(input_: ResumeInput) -> FailureEvidence:
    """续跑的首轮**必须**带失败证据。

    循环体的消息选择是「有证据 → 修复消息，否则 → 首轮消息」；续跑若拿不到证据就会退回
    `build_first_message`，等于把首轮消息重发一遍（界面点"继续修复"却看到模型从头再来）。
    所以调用方没给证据时补一个**不含任何编造细节**的占位：round 取上一轮、阶段取 execute，
    三个细节字段全空 —— `build_repair_message` 对空细节是健壮的，渲染出来只有「骨架 + 锚点 +
    只修复上述问题」，不会凭空声称某个退出码或某条发现。
    """
    if input_.evidence is not None:
        return input_.evidence
    return FailureEvidence(round=max(input_.start_round - 1, 0), stage="execute")


def resume_repair(input_: ResumeInput) -> LoopResult:
    """从 `start_round` 起续跑修复循环，直到成功、轮次用尽或取消。

    与 `run_loop` 的唯一区别：**不调用 `ports.opencode.start()`**，直接复用 `session_id`；
    首轮消息用 `build_repair_message`（因为已经有失败证据）而不是 `build_first_message`。
    预检、输入落盘、轮次循环、落盘与 meta 记账全部与 `run_loop` 共用同一批辅助。
    """
    ports = input_.ports
    config = input_.config

    skeleton = render_template(
        input_.template.body, list(input_.template.placeholders), input_.values
    )
    anchors = (
        input_.template.anchors if input_.template.anchors else extract_anchors(skeleton)
    )

    detection, aborted = _precheck(
        ports=ports, run_dir=input_.run_dir, plan=input_.plan, skeleton=skeleton
    )
    if aborted is not None:
        return aborted

    write_meta = _make_meta_writer(
        ports,
        input_.run_dir,
        {
            "sessionId": input_.session_id,
            "config": _jsonable(config),
            "detection": _jsonable(detection),
        },
    )
    return _drive_loop(
        ports=ports,
        config=config,
        session_id=input_.session_id,
        skeleton=skeleton,
        anchors=anchors,
        plan=input_.plan,
        run_dir=input_.run_dir,
        template=input_.template,
        cancel=input_.cancel,
        first_round=input_.start_round,
        evidence=_resume_evidence(input_),
        write_meta=write_meta,
    )
