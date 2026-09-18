import json
from dataclasses import dataclass, field

from tu_shell_agent.orchestrator.loop import (
    LoopInput,
    LoopPorts,
    VerifyInput,
    run_loop,
    verify_and_execute,
)
from tu_shell_agent.types import (
    DetectionReport,
    ExecuteResult,
    GeneratedScript,
    RunConfig,
    ShellcheckFinding,
)

SKELETON = "#!/usr/bin/env bash\nset -euo pipefail\n# @@TU:BODY@@\necho ok\n"
GOOD = GeneratedScript(script=SKELETON, notes="", assumptions=())


@dataclass
class FakeTemplate:
    id: str = "single"
    body: str = SKELETON
    anchors: tuple[str, ...] = ("@@TU:BODY@@",)
    trusted: bool = True
    placeholders: tuple = ()


@dataclass
class Harness:
    scripts: list[str] = field(default_factory=list)
    written: list[str] = field(default_factory=list)
    prompts: list[str] = field(default_factory=list)
    confirmed: int = 0
    events: list = field(default_factory=list)
    attempts: list = field(default_factory=list)
    metas: list = field(default_factory=list)
    inputs: list = field(default_factory=list)


def make_ports(
    rounds: list[GeneratedScript],
    shellcheck_for=None,
    execute_for=None,
    confirm: bool = True,
    start_error: Exception | None = None,
    seen: Harness | None = None,
):
    harness = seen or Harness()
    turn = {"n": 0}

    class FakeOpencode:
        def start(self, run_dir, agent_name, model):
            if start_error is not None:
                raise start_error
            return "ses_1"

        def generate(self, session_id, message, schema, timeout_ms, on_delta=None, cancel=None):
            harness.prompts.append(message)
            result = rounds[min(turn["n"], len(rounds) - 1)]
            turn["n"] += 1
            harness.scripts.append(result.script)
            return result

        def abort(self, session_id):
            return None

        def dispose(self):
            return None

    class FakeToolchain:
        def detect(self):
            return DetectionReport(None, None, None, ())

        def shellcheck(self, script_path):
            findings = shellcheck_for(harness.scripts[-1]) if shellcheck_for else []
            return list(findings), 0, '{"comments": []}'

        def execute(self, script_path, cwd, timeout_ms, cancel=None, on_stdout=None, on_stderr=None):
            if execute_for is not None:
                return execute_for(harness.scripts[-1])
            return ExecuteResult(0, None, False, False, 1, "ok\n", "")

    class FakeConfirm:
        def confirm(self, round_no, script_path, script, trusted):
            harness.confirmed += 1
            return confirm

    class FakeStore:
        run_dir = "/tmp/run"

        def write_script(self, round_no, script):
            harness.written.append(f"{round_no}:{script}")
            return "/tmp/run/script.sh"

        def write_inputs(self, files):
            harness.inputs.append(dict(files))
            return None

        def write_attempt(self, round_no, files):
            harness.attempts.append((round_no, dict(files)))
            return None

        def write_meta(self, patch):
            harness.metas.append(dict(patch))
            return None

    return {
        "opencode": FakeOpencode(),
        "toolchain": FakeToolchain(),
        "confirm": FakeConfirm(),
        "store": FakeStore(),
        "emit": harness.events.append,
    }, harness


CONFIG = RunConfig(run_root="/tmp/root", max_rounds=3, generate_timeout_ms=5000, execute_timeout_ms=5000)


def run(input_ports, template=None, plan="方案", cancel=None):
    return run_loop(
        LoopInput(
            plan=plan,
            template=template or FakeTemplate(),
            values={},
            run_dir="/tmp/run",
            config=CONFIG,
            # make_ports 返回的是 {opencode, toolchain, confirm, store, emit} 映射（字段名与
            # LoopPorts 一一对应）；LoopInput.ports 的类型就是 LoopPorts，这里显式组装，
            # 让编排层只做属性访问，不必为映射兜底。
            ports=LoopPorts(**input_ports),
            cancel=cancel,
        )
    )


def contains_meta(harness, expected: dict) -> bool:
    """meta.json 现在是"超集"（含 runId/sessionId/config/detection 快照），
    所以断言要按子集匹配，不能再要求整字典相等。"""
    return any(all(patch.get(key) == value for key, value in expected.items())
               for patch in harness.metas)


def _attempt_file(harness, name: str) -> str:
    """按文件名取出 write_attempt 写入的内容（不依赖同一轮内的调用次序）。"""
    return next(files[name] for _round, files in harness.attempts if name in files)


def _attempt_names(harness) -> set[str]:
    """write_attempt 写过的全部文件名（不依赖同一轮内的调用次序）。

    注意与 _attempt_file 的区别：后者返回**文件内容**，所以「文件名是否被写入过」
    必须查这个集合，写成 `name in _attempt_file(harness, name)` 是在内容里找文件名，
    恒为 False。
    """
    return {name for _round, files in harness.attempts for name in files}


def test_first_round_pass_succeeds_and_writes_once():
    ports, harness = make_ports([GOOD])
    result = run(ports)
    assert result.outcome == "succeeded"
    assert result.rounds == 1
    assert len(harness.written) == 1


def test_shellcheck_failure_then_fix_succeeds_on_round_two():
    broken = GeneratedScript(script='#!/usr/bin/env bash\n# @@TU:BODY@@\nf="a b"\nls $f\n', notes="", assumptions=())

    def shellcheck_for(script: str):
        if "ls $f" in script:
            return [ShellcheckFinding("SC2086", 4, 4, "warning", "Double quote")]
        return []

    ports, harness = make_ports([broken, GOOD], shellcheck_for=shellcheck_for)
    result = run(ports)
    assert result.outcome == "succeeded"
    assert result.rounds == 2
    assert "SC2086" in harness.prompts[1]


def test_execute_failure_feeds_exit_code_and_stderr_forward():
    failing = GeneratedScript(script="#!/usr/bin/env bash\n# @@TU:BODY@@\nexit 3\n", notes="", assumptions=())

    def execute_for(script: str):
        if "exit 3" in script:
            return ExecuteResult(3, None, False, False, 5, "", "缺少输入文件\n")
        return ExecuteResult(0, None, False, False, 5, "ok\n", "")

    ports, harness = make_ports([failing, GOOD], execute_for=execute_for)
    result = run(ports)
    assert result.outcome == "succeeded"
    assert "退出码 3" in harness.prompts[1]
    assert "缺少输入文件" in harness.prompts[1]


def test_generation_error_is_fed_back_and_next_round_succeeds():
    ports, harness = make_ports([GOOD])
    calls = {"n": 0}

    def failing_generate(session_id, message, schema, timeout_ms, on_delta=None, cancel=None):
        calls["n"] += 1
        harness.prompts.append(message)
        if calls["n"] == 1:
            raise RuntimeError("StructuredOutputError: 模型未按 schema 返回")
        return GOOD

    ports["opencode"].generate = failing_generate
    result = run(ports)
    assert result.outcome == "succeeded"
    assert result.rounds == 2
    assert "StructuredOutputError" in harness.prompts[1]


def test_start_failure_returns_aborted_dependency():
    ports, harness = make_ports([GOOD], start_error=RuntimeError("health 不通"))
    result = run(ports)
    assert result.outcome == "aborted_dependency"
    assert result.rounds == 0
    assert ("start-error.txt" in _attempt_names(harness)) is True
    assert contains_meta(harness, {"outcome": "aborted_dependency", "rounds": 0})


def test_shellcheck_dependency_failure_aborts_without_repair_loop():
    """shellcheck 自己坏了（文件读不了/参数错）→ aborted_dependency，不烧修复轮次。"""
    ports, harness = make_ports([GOOD])

    def shellcheck_that_raises(_script_path):
        raise RuntimeError("shellcheck 调用失败（退出码 2，文件无法处理）")

    ports["toolchain"].shellcheck = shellcheck_that_raises
    result = run(ports)
    assert result.outcome == "aborted_dependency"
    assert result.rounds == 1
    assert len(harness.prompts) == 1
    assert ("shellcheck-error.txt" in _attempt_names(harness)) is True
    assert contains_meta(harness, {"outcome": "aborted_dependency", "rounds": 1})


def test_execute_dependency_failure_aborts_instead_of_raising():
    """bash 在执行阶段消失 → aborted_dependency，异常不得穿出编排层（规格 §6）。"""
    ports, harness = make_ports([GOOD])

    def execute_that_raises(*_args, **_kwargs):
        raise FileNotFoundError("bash 不见了")

    ports["toolchain"].execute = execute_that_raises
    result = run(ports)
    assert result.outcome == "aborted_dependency"
    assert result.rounds == 1
    assert len(harness.prompts) == 1
    assert ("execute-error.txt" in _attempt_names(harness)) is True
    assert contains_meta(harness, {"outcome": "aborted_dependency", "rounds": 1})


def test_three_failing_rounds_end_as_needs_human():
    broken = GeneratedScript(script="#!/usr/bin/env bash\n# @@TU:BODY@@\nls $f\n", notes="", assumptions=())
    ports, harness = make_ports(
        [broken],
        shellcheck_for=lambda _s: [ShellcheckFinding("SC2086", 3, 4, "warning", "q")],
    )
    result = run(ports)
    assert result.outcome == "needs_human"
    assert result.rounds == 3
    assert len(harness.prompts) == 3


def test_user_rejection_cancels_without_next_round():
    ports, harness = make_ports([GOOD], confirm=False)
    # 必须显式用非信任模板：信任模板会短路掉确认（approved = trusted or confirm(...)），
    # 那样 confirm 根本不会被问到，这个测试就测不到「用户拒绝」这条路径。
    result = run(ports, template=FakeTemplate(trusted=False))
    assert result.outcome == "cancelled"
    assert len(harness.prompts) == 1


def test_missing_anchor_reports_contract_failure_into_next_prompt():
    no_anchor = GeneratedScript(script="#!/usr/bin/env bash\necho hi\n", notes="", assumptions=())
    ports, harness = make_ports([no_anchor, GOOD])
    result = run(ports)
    assert result.outcome == "succeeded"
    assert "@@TU:BODY@@" in harness.prompts[1]


def test_style_level_findings_do_not_block():
    ports, _harness = make_ports(
        [GOOD], shellcheck_for=lambda _s: [ShellcheckFinding("SC2006", 1, 1, "style", "use $()")]
    )
    assert run(ports).outcome == "succeeded"


def test_info_level_findings_block_at_the_default_level():
    """默认阻断级别是 info（实测 SC2086 就是 info 级），所以 info 必须触发回灌修复。"""
    broken = GeneratedScript(
        script="#!/usr/bin/env bash\n# @@TU:BODY@@\necho $f\n", notes="", assumptions=()
    )
    ports, harness = make_ports(
        [broken, GOOD],
        shellcheck_for=lambda s: (
            [ShellcheckFinding("SC2086", 3, 6, "info", "quote it")] if "echo $f" in s else []
        ),
    )
    result = run(ports)
    assert result.outcome == "succeeded"
    assert result.rounds == 2
    assert "SC2086" in harness.prompts[1]


def test_trusted_template_never_asks_for_confirmation():
    ports, harness = make_ports([GOOD])
    run(ports, template=FakeTemplate(trusted=True))
    assert harness.confirmed == 0


def test_untrusted_template_asks_once():
    ports, harness = make_ports([GOOD])
    run(ports, template=FakeTemplate(trusted=False))
    assert harness.confirmed == 1


def test_timeout_result_is_not_treated_as_success():
    """SIGKILL 的脚本表现为 exit_code=-9；必须靠 timed_out 判失败，回灌提示含「超时被杀」。"""
    ports, harness = make_ports(
        [GOOD, GOOD],
        execute_for=lambda _script: ExecuteResult(-9, None, True, False, 400, "", ""),
    )
    result = run(ports)
    assert result.outcome != "succeeded"
    assert result.outcome == "needs_human"
    assert "超时被杀" in harness.prompts[1]


def test_cancelled_result_short_circuits_without_next_round():
    ports, harness = make_ports(
        [GOOD],
        execute_for=lambda _script: ExecuteResult(None, None, False, True, 120, "", ""),
    )
    result = run(ports)
    assert result.outcome == "cancelled"
    assert len(harness.prompts) == 1


def test_cancel_token_stops_before_first_generation():
    class Cancel:
        def is_set(self):
            return True

    ports, harness = make_ports([GOOD])
    result = run(ports, cancel=Cancel())
    assert result.outcome == "cancelled"
    assert result.rounds == 0
    assert harness.prompts == []
    assert contains_meta(harness, {"outcome": "cancelled", "rounds": 0})


def test_execute_json_is_valid_json_even_when_cancelled():
    """execute.json 必须始终是**合法 JSON**：取消路径 exit_code=None，手写 f-string 会写成
    `"exit_code": None`（JSON 里应为 null），后续 json.loads 回放会直接抛 JSONDecodeError。"""
    ports, harness = make_ports(
        [GOOD],
        execute_for=lambda _script: ExecuteResult(None, None, False, True, 120, "", ""),
    )
    run(ports)
    payload = json.loads(_attempt_file(harness, "execute.json"))
    assert payload["exit_code"] is None
    assert payload["cancelled"] is True


# ── F3：输入落盘 + meta.json 快照（规格 §10）────────────────────────────────


def test_inputs_and_meta_snapshot_are_written():
    """预检通过后写 plan.md / template.sh；meta.json 含 sessionId / 配置快照 / 自检结果。"""
    ports, harness = make_ports([GOOD])
    run(ports, plan="把日志按时间倒序列出")

    assert harness.inputs, "必须调用 write_inputs"
    files = harness.inputs[0]
    assert set(files) == {"plan.md", "template.sh"}
    assert files["plan.md"] == "把日志按时间倒序列出"  # 方案全文原样落盘
    assert "@@TU:BODY@@" in files["template.sh"]  # 渲染后的骨架

    meta = harness.metas[-1]
    assert meta["outcome"] == "succeeded"
    assert meta["sessionId"] == "ses_1"
    assert meta["runId"] == "run"  # 由 run_dir 的 basename 推出
    assert meta["config"]["max_rounds"] == 3  # 配置快照是 dict，不是 dataclass 对象
    assert isinstance(meta["detection"], dict) and "problems" in meta["detection"]
    # 整份 meta 必须真的能序列化（塞 dataclass 对象会在 json.dumps 处 TypeError）
    assert json.dumps(meta, ensure_ascii=False)


# ── F2：生成阶段取消不是"生成失败"──────────────────────────────────────────


class _Token:
    def __init__(self) -> None:
        self._set = False

    def set(self) -> None:
        self._set = True

    def is_set(self) -> bool:
        return self._set


def test_cancel_during_generation_returns_cancelled_without_burning_a_round():
    """生成期间取消 → outcome=cancelled，不能计一次契约失败白烧轮次。"""
    ports, harness = make_ports([GOOD])
    token = _Token()

    def generate_that_cancels(session_id, message, schema, timeout_ms, on_delta=None, cancel=None):
        harness.prompts.append(message)
        token.set()  # 用户在生成期间按了取消
        raise RuntimeError("已取消")

    ports["opencode"].generate = generate_that_cancels
    result = run(ports, cancel=token)

    assert result.outcome == "cancelled"
    assert result.rounds == 0  # 这一轮没被算进去
    assert len(harness.prompts) == 1  # 没有第二轮
    assert "generation-error.txt" not in _attempt_names(harness)  # 不留"生成失败"证据
    assert harness.metas[-1]["outcome"] == "cancelled"


def test_generation_failure_without_cancel_still_burns_a_round():
    """反向对照：没有取消令牌时，生成异常仍按契约失败回灌（防止把失败都当取消）。"""
    ports, harness = make_ports([GOOD])
    calls = {"n": 0}

    def flaky(session_id, message, schema, timeout_ms, on_delta=None, cancel=None):
        calls["n"] += 1
        harness.prompts.append(message)
        if calls["n"] == 1:
            raise RuntimeError("结构化输出失败")
        return GOOD

    ports["opencode"].generate = flaky
    result = run(ports)
    assert result.outcome == "succeeded"
    assert result.rounds == 2
    assert "generation-error.txt" in _attempt_names(harness)


# ── F7：detect 端口异常不得穿出编排层 ──────────────────────────────────────


def test_detect_failure_aborts_instead_of_raising():
    """detect 是唯一没有兜底的端口调用：抛异常要变成 aborted_dependency。"""
    ports, harness = make_ports([GOOD])

    def detect_that_raises():
        raise OSError("探测子进程崩了")

    ports["toolchain"].detect = detect_that_raises
    result = run(ports)  # 不得抛异常

    assert result.outcome == "aborted_dependency"
    assert result.rounds == 0
    assert harness.prompts == []  # 没进生成
    assert "detect-error.txt" in _attempt_names(harness)
    assert any(patch.get("outcome") == "aborted_dependency" for patch in harness.metas)


def test_detect_problems_also_writes_terminal_meta():
    """detect **报 problems**（而不是抛异常）也要落终态。

    这条分支此前不写 meta.json，于是运行目录里会留下"有目录、无 meta.json"的运行，
    Plan 2 的历史列表读到会缺结论；现在与"detect 抛异常"那条分支对齐。
    """
    ports, harness = make_ports([GOOD])
    ports["toolchain"].detect = lambda: DetectionReport(
        None, None, None, ("缺 bash", "缺 shellcheck")
    )

    result = run(ports)

    assert result.outcome == "aborted_dependency"
    assert result.rounds == 0
    assert harness.prompts == []  # 预检没过，不进生成
    assert "detect-error.txt" not in _attempt_names(harness)  # 不是"探测崩了"，别留错证据
    assert contains_meta(harness, {"outcome": "aborted_dependency", "rounds": 0})


def test_verify_and_execute_normalizes_crlf_script_in_place(tmp_path):
    """规格 §11：脚本文件不得含 \\r（Git Bash 会报 \\r 错）。

    run_loop 路径靠写盘时 `to_lf` 保证这一点；verify_and_execute 校验的是**用户手工改过的
    既有文件**，Windows 上的编辑器很容易把它存成 CRLF，所以入口必须补一次归一化 ——
    否则用户在界面上点"重跑改过的脚本"，会看到"脚本莫名失败"而找不到原因。
    """
    script = tmp_path / "script.sh"
    script.write_bytes(b'#!/usr/bin/env bash\r\n# @@TU:BODY@@\r\necho "ok"\r\n')
    ports, harness = make_ports([GOOD])

    result = verify_and_execute(
        VerifyInput(
            script_path=str(script),
            run_dir=str(tmp_path),
            round_no=1,
            config=CONFIG,
            ports=LoopPorts(**ports),
        )
    )

    assert result.outcome == "succeeded"
    # 必须用 read_bytes 断言：read_text 的通用换行会把 \r\n 悄悄读成 \n，那样即使磁盘上仍是
    # CRLF 这条断言也会通过（第一版就踩了这个坑：归一化成了死代码而测试却是绿的）。
    assert b"\r" not in script.read_bytes()
    # 除换行外内容一字不动（只去 \r，不重排、不裁剪、不翻译）
    assert script.read_bytes() == b'#!/usr/bin/env bash\n# @@TU:BODY@@\necho "ok"\n'
    assert "normalized.txt" in _attempt_names(harness)  # 归一化这件事留下证据
