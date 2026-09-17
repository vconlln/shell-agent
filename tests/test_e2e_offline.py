"""离线全链路：真实 shellcheck + 真实 bash + 假 opencode。"""

import json
from dataclasses import dataclass, field
from pathlib import Path

from tu_shell_agent.orchestrator.loop import LoopInput, LoopPorts, TemplateSpec, run_loop
from tu_shell_agent.run_store.store import RunStore
from tu_shell_agent.shell_toolchain.shellcheck import run_shellcheck
from tu_shell_agent.types import (
    DetectionReport,
    ExecuteResult,
    GeneratedScript,
    RunConfig,
)

from tu_shell_agent.shell_toolchain.execute import run_script

# 夹具设计依据（实测 shellcheck 0.11.0 的级别，不要随手改）：
#   for f in $(ls)     → SC2045 error   ← 在任何阻断阈值下都会拦下，与默认值解耦，故用它做阻断点
#   echo $f（未加引号）→ SC2086 info    ← 默认阻断级别 warning 拦不住，不能当阻断点
#   echo done          → SC1010 warning ← 对字面量词的误报，修好的脚本里必须避免（改 echo "done"）
BROKEN = (
    "#!/usr/bin/env bash\nset -euo pipefail\n# @@TU:BODY@@\n"
    'for f in $(ls); do echo $f; done\necho "done"\n'
)
FIXED = (
    "#!/usr/bin/env bash\nset -euo pipefail\n# @@TU:BODY@@\n"
    'for f in *; do echo "$f"; done\necho "done"\n'
)


@dataclass
class FakeOpencode:
    turn: int = 0

    def start(self, run_dir, agent_name, model):
        return "ses_offline"

    def generate(self, session_id, message, schema, timeout_ms, on_delta=None, cancel=None):
        self.turn += 1
        if self.turn == 1:
            return GeneratedScript(BROKEN, "首轮", ())
        return GeneratedScript(FIXED, "补引号", ())

    def abort(self, session_id):
        return None

    def dispose(self):
        return None


def test_broken_script_is_caught_then_fixed_and_executed(tmp_path, shellcheck_path, bash_path):
    run_dir = str(tmp_path / "r1")
    store = RunStore(run_dir)
    store.init()

    class Toolchain:
        def detect(self):
            return DetectionReport(None, None, None, ())

        def shellcheck(self, script_path):
            return run_shellcheck(shellcheck_path, script_path)

        def execute(self, script_path, cwd, timeout_ms, cancel=None, on_stdout=None, on_stderr=None):
            return run_script(bash_path, script_path, cwd, timeout_ms)

    ports = LoopPorts(
        opencode=FakeOpencode(),
        toolchain=Toolchain(),
        confirm=type("C", (), {"confirm": lambda self, *a: True})(),
        store=store,
        emit=lambda event: None,
    )

    result = run_loop(
        LoopInput(
            plan="把一句话拆成单词逐行打印",
            template=TemplateSpec(
                id="single",
                body="#!/usr/bin/env bash\nset -euo pipefail\n# @@TU:BODY@@\n",
                anchors=("@@TU:BODY@@",),
                trusted=True,
            ),
            values={},
            run_dir=run_dir,
            config=RunConfig(
                run_root=str(tmp_path), max_rounds=3,
                generate_timeout_ms=5000, execute_timeout_ms=10_000,
            ),
            ports=ports,
        )
    )

    assert result.outcome == "succeeded"
    assert result.rounds == 2
    attempt_one = tmp_path / "r1" / "attempts" / "1"
    # 读 shellcheck.txt 而不是 shellcheck.json：json 是 shellcheck 的原始 json1，
    # code 字段是裸数字（"code":2045），带 SC 前缀的形态只出现在渲染后的报告里。
    assert "SC2045" in (attempt_one / "shellcheck.txt").read_text(encoding="utf-8")
    attempt_two_stdout = (tmp_path / "r1" / "attempts" / "2" / "stdout.txt").read_text(
        encoding="utf-8"
    )
    # 运行目录里必然有 script.sh，被 `for f in *` 列出来 —— 用它做确定性断言，别依赖目录内容
    assert "script.sh" in attempt_two_stdout
    assert "done" in attempt_two_stdout
    assert '"succeeded"' in (tmp_path / "r1" / "meta.json").read_text(encoding="utf-8")

    # 规格 §10：运行目录要自包含可回放 —— 输入落盘 + meta.json 带配置快照与 sessionId。
    # （这里用的是真实 RunStore，不是假 store，所以覆盖的是真实落盘路径。）
    assert (tmp_path / "r1" / "plan.md").read_text(encoding="utf-8") == "把一句话拆成单词逐行打印"
    assert "@@TU:BODY@@" in (tmp_path / "r1" / "template.sh").read_text(encoding="utf-8")
    meta = json.loads((tmp_path / "r1" / "meta.json").read_text(encoding="utf-8"))
    assert meta["sessionId"] == "ses_offline"
    assert meta["config"]["max_rounds"] == 3 and meta["runId"] == "r1"


def test_path_overrides_are_absolute():
    """CLI 的 --*-path 必须 resolve 成绝对路径、并展开 ~。

    server.start_serve 用 cwd=run_dir 起子进程，相对路径会在新 cwd 下解析不到
    （实测 `--opencode-path tools/opencode` → [Errno 2] → aborted_dependency(0 轮)，
    而 CLI 自检却是通过的）。不展开 ~ 更隐蔽：会 resolve 成字面 `cwd/~/...`，
    探测失败后静默回退 PATH，用户以为覆盖生效了。这条测试锁住这两个回归。
    """
    from tu_shell_agent.cli import _parse_args, _path_overrides

    args = _parse_args([
        "--plan", "p.md",
        "--opencode-path", "tools/opencode",
        "--shellcheck-path", "./tools/shellcheck",
    ])
    overrides = _path_overrides(args)
    assert all(Path(value).is_absolute() for value in overrides.values())
    assert "bash" not in overrides  # bash 未传则不应出现键

    # ~ 必须展开：结果里不能再有字面 "~"，且仍为绝对路径
    home_args = _parse_args(["--plan", "p.md", "--opencode-path", "~/tools/opencode"])
    home_overrides = _path_overrides(home_args)
    expanded = home_overrides["opencode"]
    assert Path(expanded).is_absolute()
    assert "~" not in expanded
    assert expanded == str(Path("~/tools/opencode").expanduser().resolve())
