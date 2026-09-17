from dataclasses import dataclass, field

from tu_shell_agent.orchestrator.loop import LoopInput, LoopPorts, run_loop
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
    assert "start-error.txt" in harness.attempts[0][1]
    assert {"outcome": "aborted_dependency", "rounds": 0} in harness.metas


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
    assert "shellcheck-error.txt" in harness.attempts[-1][1]
    assert {"outcome": "aborted_dependency", "rounds": 1} in harness.metas


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
    assert "execute-error.txt" in harness.attempts[-1][1]
    assert {"outcome": "aborted_dependency", "rounds": 1} in harness.metas


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


def test_info_level_findings_do_not_block():
    ports, _harness = make_ports(
        [GOOD], shellcheck_for=lambda _s: [ShellcheckFinding("SC2006", 1, 1, "info", "use $()")]
    )
    assert run(ports).outcome == "succeeded"


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
    assert {"outcome": "cancelled", "rounds": 0} in harness.metas
