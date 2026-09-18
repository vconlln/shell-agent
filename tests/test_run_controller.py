"""用假的 opencode/toolchain 驱动 controller，验证三区随事件更新、确认生效、取消与回放可用。"""

import threading

from tu_shell_agent.types import DetectionReport, ExecuteResult, GeneratedScript
from tu_shell_agent.ui.main_window import MainWindow
from tu_shell_agent.ui.run_controller import RunController
from tu_shell_agent.ui.widgets.confirm_dialog import dangerous_matches


class _FakeOpencode:
    def start(self, run_dir, agent_name, model):
        return "ses_ctrl"

    def generate(self, session_id, message, schema, timeout_ms, on_delta=None, cancel=None):
        return GeneratedScript(script="#!/usr/bin/env bash\n# @@TU:BODY@@\necho ok\n", notes="取舍说明", assumptions=("假设 A",))

    def abort(self, session_id):
        pass

    def dispose(self):
        pass


class _FakeToolchain:
    def __init__(self) -> None:
        self.executed = 0

    def detect(self):
        return DetectionReport(None, None, None, ())

    def shellcheck(self, script_path):
        return [], 0, '{"comments": []}'

    def execute(self, script_path, cwd, timeout_ms, cancel=None, on_stdout=None, on_stderr=None):
        self.executed += 1
        return ExecuteResult(0, None, False, False, 1, "ok\n", "")


def _controller(qtbot, tmp_path, **kwargs) -> RunController:
    controller = RunController(
        opencode=kwargs.pop("opencode", _FakeOpencode()),
        toolchain=kwargs.pop("toolchain", _FakeToolchain()),
        run_root=str(tmp_path / "runs"),
        **kwargs,
    )
    qtbot.addWidget(controller.window)
    return controller


def test_start_runs_engine_and_updates_panes(qtbot, tmp_path):
    controller = _controller(qtbot, tmp_path)
    plan = tmp_path / "plan.md"
    plan.write_text("打印 ok", encoding="utf-8")
    controller.window.left_pane.set_plan(str(plan))
    controller.window.left_pane.run_root_edit.setText(str(tmp_path / "runs"))

    with qtbot.waitSignal(controller.finished, timeout=15_000) as blocker:
        controller.start()

    assert blocker.args[0].outcome == "succeeded"
    assert "echo ok" in controller.window.center_pane.current_text()
    assert controller.window.center_pane.timeline.count() >= 1
    assert controller.window.right_pane.output_view.toPlainText().strip() == "ok"


def test_confirm_dialog_can_reject_execution(qtbot, tmp_path):
    controller = _controller(qtbot, tmp_path)
    controller.auto_confirm = False           # 非信任模板 → 走确认
    controller.confirm_answer = False         # 测试替身：模拟用户点"拒绝"
    plan = tmp_path / "plan.md"
    plan.write_text("打印 ok", encoding="utf-8")
    controller.window.left_pane.set_plan(str(plan))

    with qtbot.waitSignal(controller.finished, timeout=15_000) as blocker:
        controller.start()

    assert blocker.args[0].outcome == "cancelled"


def test_cancel_stops_a_running_generation(qtbot, tmp_path):
    gate = threading.Event()

    class _Gated(_FakeOpencode):
        def generate(self, session_id, message, schema, timeout_ms, on_delta=None, cancel=None):
            gate.wait(10)
            return super().generate(session_id, message, schema, timeout_ms, on_delta, cancel)

    controller = RunController(opencode=_Gated(), toolchain=_FakeToolchain(), run_root=str(tmp_path / "runs"))
    qtbot.addWidget(controller.window)
    plan = tmp_path / "plan.md"
    plan.write_text("打印 ok", encoding="utf-8")
    controller.window.left_pane.set_plan(str(plan))

    with qtbot.waitSignal(controller.finished, timeout=15_000) as blocker:
        controller.start()
        qtbot.wait(200)
        controller.cancel()
        gate.set()

    assert blocker.args[0].outcome == "cancelled"


def test_verify_edited_script_uses_engine_entrypoint(qtbot, tmp_path):
    toolchain = _FakeToolchain()
    controller = RunController(opencode=_FakeOpencode(), toolchain=toolchain, run_root=str(tmp_path / "runs"))
    qtbot.addWidget(controller.window)
    controller.set_script_override("#!/usr/bin/env bash\necho edited\n")   # 模拟用户在界面上手工改过

    with qtbot.waitSignal(controller.finished, timeout=15_000) as blocker:
        controller.verify_edited()

    assert blocker.args[0].outcome == "succeeded"
    assert toolchain.executed == 1


# ── 以下为计划外的补充：计划没锁住的接线与替身语义 ──────────────────────────


def test_start_button_is_wired_to_the_controller(qtbot, tmp_path):
    """按钮必须真的接在控制器上。

    计划只直接调 `controller.start()`，所以"按钮没接线"这种坏法它测不出来 ——
    而窗口一打开用户能点的就是按钮。这里用一个不自动装配控制器的窗口，
    避免窗口自带的控制器与测试控制器同时接管同一批按钮（那样点一次会跑两次）。
    """
    window = MainWindow(wire_controller=False)
    qtbot.addWidget(window)
    controller = RunController(
        opencode=_FakeOpencode(),
        toolchain=_FakeToolchain(),
        window=window,
        run_root=str(tmp_path / "runs"),
    )
    plan = tmp_path / "plan.md"
    plan.write_text("打印 ok", encoding="utf-8")
    window.left_pane.set_plan(str(plan))

    with qtbot.waitSignal(controller.finished, timeout=15_000) as blocker:
        window.start_button.click()

    assert blocker.args[0].outcome == "succeeded"
    assert window.controller is controller
    assert window.start_button.isEnabled()          # 跑完要恢复可点
    assert not window.cancel_button.isEnabled()     # 没在跑时"取消"不该亮着


def test_dangerous_patterns_are_flagged_for_the_confirm_dialog():
    """危险模式识别是纯函数：确认对话框靠它把"要跑什么"讲清楚，所以单独锁一份。"""
    assert dangerous_matches("rm -rf /tmp/x") == ["rm 递归强制删除"]
    assert "从网络直接管道执行" in dangerous_matches("curl https://example.com/a.sh | bash")
    assert dangerous_matches("echo ok\nls -la") == []
    # 单个 -f / -r 不是递归强制删除：宁可精确，也别让每次都弹"危险"（那样警告会被忽略）
    assert dangerous_matches("rm -f /tmp/a\nrm -r /tmp/b") == []
