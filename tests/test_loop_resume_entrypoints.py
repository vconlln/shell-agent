# tests/test_loop_resume_entrypoints.py
"""新增的两个入口：手工改脚本后只重跑校验/执行；以及在既有会话上从第 n 轮继续修。

本文件**自带最小 fake**（不从其它测试文件 import）——跨文件 import 依赖 pytest 的
import 模式，脆弱；多写 40 行换取确定性是划算的。
"""

from dataclasses import dataclass, field
from pathlib import Path

from tu_shell_agent.orchestrator.loop import (
    LoopPorts,
    ResumeInput,
    TemplateSpec,
    VerifyInput,
    resume_repair,
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
CONFIG_ARGS = {"run_root": "/tmp/root", "max_rounds": 3,
               "generate_timeout_ms": 5000, "execute_timeout_ms": 5000}

TEMPLATE = TemplateSpec(id="single", body=SKELETON, anchors=("@@TU:BODY@@",), trusted=True)


@dataclass
class Harness:
    scripts: list[str] = field(default_factory=list)
    prompts: list[str] = field(default_factory=list)
    attempts: list = field(default_factory=list)
    metas: list = field(default_factory=list)


def make_ports(rounds, shellcheck_for=None, execute_for=None, confirm=True):
    """最小 fake 集合：只实现本文件用到的能力。"""
    harness = Harness()
    turn = {"n": 0}

    class FakeOpencode:
        def start(self, run_dir, agent_name, model):
            return "ses_fake"

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
            if shellcheck_for is None:
                return [], 0, '{"comments": []}'
            # 有生成过就用本轮产出的脚本；verify_and_execute 不生成，harness.scripts 是空的，
            # 这时退回读"被校验的那份脚本"（就是磁盘上 script_path 的内容）。
            # 直接写 shellcheck_for(harness.scripts[-1]) 会在 verify 路径上 IndexError。
            script = (
                harness.scripts[-1]
                if harness.scripts
                else Path(script_path).read_text(encoding="utf-8")
            )
            return list(shellcheck_for(script)), 0, '{"comments": []}'

        def execute(self, script_path, cwd, timeout_ms, cancel=None, on_stdout=None, on_stderr=None):
            if execute_for is not None:
                return execute_for(harness.scripts[-1])
            return ExecuteResult(0, None, False, False, 1, "ok\n", "")

    class FakeConfirm:
        def confirm(self, round_no, script_path, script, trusted):
            return confirm

    class FakeStore:
        run_dir = "/tmp/run"

        def write_script(self, round_no, script):
            return "/tmp/run/script.sh"

        def write_attempt(self, round_no, files):
            harness.attempts.append((round_no, dict(files)))

        def write_meta(self, patch):
            harness.metas.append(dict(patch))

        def write_inputs(self, files):
            harness.attempts.append((0, dict(files)))

    ports = LoopPorts(
        opencode=FakeOpencode(),
        toolchain=FakeToolchain(),
        confirm=FakeConfirm(),
        store=FakeStore(),
        emit=lambda _event: None,
    )
    return ports, harness


def _attempt_names(harness) -> set[str]:
    return {name for _round, files in harness.attempts for name in files}


def test_verify_and_execute_runs_shellcheck_then_execute(tmp_path):
    script = tmp_path / "script.sh"
    script.write_text('#!/usr/bin/env bash\n# @@TU:BODY@@\necho "ok"\n', encoding="utf-8")
    ports, harness = make_ports([GOOD])

    result = verify_and_execute(
        VerifyInput(
            script_path=str(script),
            run_dir=str(tmp_path),
            round_no=3,
            config=RunConfig(**CONFIG_ARGS),
            ports=ports,
        )
    )

    assert result.outcome == "succeeded"
    assert result.rounds == 3
    # 关键：这一步不该调用 opencode（不生成、不烧轮次）
    assert harness.prompts == []
    assert ("shellcheck.txt" in _attempt_names(harness)) is True


def test_verify_and_execute_blocks_on_shellcheck_findings(tmp_path):
    script = tmp_path / "script.sh"
    script.write_text('#!/usr/bin/env bash\nfor f in $(ls); do echo $f; done\n', encoding="utf-8")
    ports, harness = make_ports(
        [GOOD],
        shellcheck_for=lambda _s: [ShellcheckFinding("SC2045", 2, 10, "error", "use glob")],
    )

    result = verify_and_execute(
        VerifyInput(
            script_path=str(script),
            run_dir=str(tmp_path),
            round_no=1,
            config=RunConfig(**CONFIG_ARGS),
            ports=ports,
        )
    )

    # 手工改过的脚本仍然不合格 → 不执行、如实报告（由界面决定是否回灌给模型）
    assert result.outcome == "needs_human"
    assert result.last_execute is None
    assert [f.code for f in result.last_findings] == ["SC2045"]


def test_verify_and_execute_respects_user_rejection(tmp_path):
    script = tmp_path / "script.sh"
    script.write_text('#!/usr/bin/env bash\necho "ok"\n', encoding="utf-8")
    ports, _harness = make_ports([GOOD], confirm=False)

    result = verify_and_execute(
        VerifyInput(
            script_path=str(script),
            run_dir=str(tmp_path),
            round_no=2,
            config=RunConfig(**CONFIG_ARGS),
            ports=ports,
        )
    )
    assert result.outcome == "cancelled"


def test_resume_repair_continues_existing_session_without_restarting_opencode(tmp_path):
    """从第 2 轮继续：不得再调 opencode.start()，且要沿用给定 session_id。"""
    ports, harness = make_ports(
        [GOOD],
        shellcheck_for=lambda s: (
            [ShellcheckFinding("SC2086", 3, 6, "info", "quote it")] if "echo $f" in s else []
        ),
    )
    started: list[str] = []
    # LoopPorts 是 frozen 的 slots dataclass（不可下标），所以按属性取端口；
    # 就地替换的是 fake 实例上的方法，frozen 只挡字段重绑定，不挡这个。
    original_start = ports.opencode.start

    def spy_start(run_dir, agent_name, model):
        started.append(run_dir)
        return original_start(run_dir, agent_name, model)

    ports.opencode.start = spy_start

    result = resume_repair(
        ResumeInput(
            plan="方案",
            template=TEMPLATE,
            values={},
            run_dir=str(tmp_path),
            session_id="ses_existing",
            start_round=2,
            evidence=None,
            config=RunConfig(**CONFIG_ARGS),
            ports=ports,
        )
    )

    assert started == []  # 没有重开会话
    assert result.outcome == "succeeded"
    assert result.rounds == 2
    assert len(harness.prompts) == 1
