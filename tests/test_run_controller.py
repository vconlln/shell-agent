"""用假的 opencode/toolchain 驱动 controller，验证三区随事件更新、确认生效、取消与回放可用。"""

import json
import threading
from pathlib import Path

from tu_shell_agent.types import DetectionReport, ExecuteResult, GeneratedScript
from tu_shell_agent.ui.main_window import MainWindow
from tu_shell_agent.ui.run_controller import RunController
from tu_shell_agent.ui.settings import AppSettings
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


def _window(qtbot, tmp_path) -> MainWindow:
    """测试专用窗口：设置指向 tmp_path。

    不显式给设置的话，MainWindow 会去读**真实用户**的设置文件（里面可能已经有
    run_root），于是测试把运行目录写进用户的家目录 —— 测试不该碰用户的真实数据。
    """
    window = MainWindow(
        wire_controller=False,
        settings=AppSettings(
            run_root=str(tmp_path / "runs"),
            templates_dir=str(tmp_path / "templates"),
        ),
    )
    qtbot.addWidget(window)
    return window


def _controller(qtbot, tmp_path, **kwargs) -> RunController:
    controller = RunController(
        opencode=kwargs.pop("opencode", _FakeOpencode()),
        toolchain=kwargs.pop("toolchain", _FakeToolchain()),
        window=_window(qtbot, tmp_path),
        run_root=str(tmp_path / "runs"),
        **kwargs,
    )
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

    controller = RunController(
        opencode=_Gated(), toolchain=_FakeToolchain(), window=_window(qtbot, tmp_path),
        run_root=str(tmp_path / "runs"),
    )
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
    controller = RunController(
        opencode=_FakeOpencode(), toolchain=toolchain, window=_window(qtbot, tmp_path),
        run_root=str(tmp_path / "runs"),
    )
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
    window = _window(qtbot, tmp_path)
    controller = RunController(
        opencode=_FakeOpencode(), toolchain=_FakeToolchain(), window=window,
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


# ── 评审发现的缺陷：每一处都留一条会真的转红的用例 ──────────────────────


class _StartFailsOpencode(_FakeOpencode):
    """opencode 起不来（没装、路径写错、没执行权限）：run_loop 会在几毫秒内返回。"""

    def start(self, run_dir, agent_name, model):
        raise RuntimeError("无法执行 opencode：权限不够")


def test_start_failure_does_not_freeze_the_window(qtbot, tmp_path):
    """引擎快速失败时界面必须恢复可用。

    原实现先 `worker.start()` 再接信号：run_loop 在 opencode 起不来时几毫秒内就返回，
    那一轮的结论会被静默丢掉 —— 状态栏永远停在"开始运行…"，四个按钮永久禁用
    （只有"取消"亮着且点了没用），只能重启应用。
    """
    controller = _controller(qtbot, tmp_path, opencode=_StartFailsOpencode())
    plan = tmp_path / "plan.md"
    plan.write_text("打印 ok", encoding="utf-8")
    controller.window.left_pane.set_plan(str(plan))

    with qtbot.waitSignal(controller.finished, timeout=15_000) as blocker:
        controller.start()

    assert blocker.args[0].outcome == "aborted_dependency"
    assert controller.window.status_label.text().startswith("结论：")
    assert controller.window.start_button.isEnabled()
    assert not controller.window.cancel_button.isEnabled()


def test_controller_connects_signals_before_starting_the_worker(qtbot, tmp_path, monkeypatch):
    """信号必须在 start() **之前**接好。

    真实事故形态：opencode 起不来时 run_loop 几毫秒内就返回，那一轮的结论会被静默丢掉，
    界面停在"开始运行…"、按钮永久禁用。为了确定性地测出连接顺序（而不是靠 99% 都跑赢的
    竞态），这里把 start() 换成一个"刚被启动就先发事件"的探针：控制器若在 start() 之后
    才连信号，Qt 不会把已发出的事件补发过来，这个事件就永远到不了。
    """
    from tu_shell_agent.types import RunEvent
    from tu_shell_agent.ui import run_controller as rc
    from tu_shell_agent.ui.engine_worker import EngineWorker

    class Probe(EngineWorker):
        def start(self) -> None:  # noqa: D102 - 探针
            self.event.emit(RunEvent("note", 0, {"message": "PROBE-在 start 当刻发出"}))
            super().start()

    monkeypatch.setattr(rc, "EngineWorker", Probe)
    controller = _controller(qtbot, tmp_path)
    seen: list = []
    controller.events.connect(lambda event: seen.append(event.payload.get("message", "")))
    plan = tmp_path / "plan.md"
    plan.write_text("打印 ok", encoding="utf-8")
    controller.window.left_pane.set_plan(str(plan))

    with qtbot.waitSignal(controller.finished, timeout=15_000):
        controller.start()

    assert any(text.startswith("PROBE-在 start 当刻发出") for text in seen), seen


def test_engine_layer_failure_emits_failed_not_finished(qtbot, tmp_path):
    """连 LoopResult 都产不出来时（线程里的意外异常）必须走 failed，不能让人一直等。"""
    controller = _controller(qtbot, tmp_path)

    def explode(*_args, **_kwargs):
        raise RuntimeError("线程里的意外异常")

    controller._worker = None
    with qtbot.waitSignal(controller.failed, timeout=15_000) as blocker:
        controller._run(explode, str(tmp_path / "run"), controller.window.left_pane.to_run_config())

    assert "线程里的意外异常" in blocker.args[0]
    assert controller.window.start_button.isEnabled()


def test_verify_edited_refuses_to_run_while_a_run_is_in_progress(qtbot, tmp_path):
    """运行中触发"改后重跑"不得改动正在跑的运行目录。

    原实现把 `store.write_script(...)` 放在忙碌守卫**之前**：正在跑的那次运行的
    script.sh（根目录与 attempts/<n>）会被用户改的文本换掉，引擎随后读到的就是它。
    """
    gate = threading.Event()

    class _Gated(_FakeOpencode):
        def generate(self, session_id, message, schema, timeout_ms, on_delta=None, cancel=None):
            gate.wait(10)
            return super().generate(session_id, message, schema, timeout_ms, on_delta, cancel)

    controller = _controller(qtbot, tmp_path, opencode=_Gated())
    plan = tmp_path / "plan.md"
    plan.write_text("打印 ok", encoding="utf-8")
    controller.window.left_pane.set_plan(str(plan))

    try:
        with qtbot.waitSignal(controller.finished, timeout=15_000) as blocker:
            controller.start()
            qtbot.wait(200)
            controller.set_script_override("#!/usr/bin/env bash\necho USER_EDIT\n")
            controller.verify_edited()      # 运行还没结束 → 必须被拒绝
            assert controller.window.status_label.text() == "上一次运行还没结束"
            # 必须**当刻**查盘：晚一点引擎自己的 write_script 会把用户改的文本盖掉，
            # 那时再查就查不出"守卫之前就写了盘"这个缺陷。
            written = [
                path for path in Path(controller._run_dir).rglob("*.sh")
                if "USER_EDIT" in path.read_text(encoding="utf-8", errors="replace")
            ]
            assert written == [], written
            gate.set()
    finally:
        gate.set()                          # 断言失败时也要放行，别把线程留在运行态

    assert blocker.args[0].outcome == "succeeded"


def test_replay_without_a_report_does_not_claim_zero_findings(qtbot, tmp_path):
    """回放一个**没留下 shellcheck 报告**的运行，不能显示成"0 处（本轮没有发现）"。

    第 1 轮契约失败就退出的运行根本没有 attempts/<n>/shellcheck.json；把"报告缺失"
    渲染成"检查过、没问题"是这份界面里最不该出现的假结论。
    """
    from tu_shell_agent.ui.run_controller import RunController

    window = _window(qtbot, tmp_path)
    RunController(
        opencode=_FakeOpencode(), toolchain=_FakeToolchain(), window=window,
        run_root=str(tmp_path / "runs"),
    )
    root = tmp_path / "runs"
    # 两份都没有可用报告：一份压根没有 shellcheck.json，一份的报告是半截 JSON
    for run_id, report in (("20260918-100000-aaaa", None), ("20260918-110000-bbbb", '{"comments": [{')):
        run_dir = root / run_id
        (run_dir / "attempts" / "1").mkdir(parents=True)
        (run_dir / "script.sh").write_text("echo replay\n", encoding="utf-8")
        (run_dir / "meta.json").write_text('{"outcome": "needs_human", "rounds": 1}', encoding="utf-8")
        if report is not None:
            (run_dir / "attempts" / "1" / "shellcheck.json").write_text(report, encoding="utf-8")

    window.history_page.run_root = str(root)
    window.history_page.reload()
    for row in range(window.history_page.list_widget.count()):
        window.history_page.list_widget.setCurrentRow(row)
        assert "echo replay" in window.center_pane.current_text()
        assert "尚未校验" in window.right_pane.findings_summary.text()
        assert "0 处" not in window.right_pane.findings_summary.text()


def test_blocking_level_shown_follows_the_run_config(qtbot, tmp_path):
    """右栏标注用的阻断级别必须是**本次运行生效**的那一个（引擎读同一份 config）。"""
    from tu_shell_agent.ui.run_controller import RunController

    window = _window(qtbot, tmp_path)
    controller = RunController(
        opencode=_FakeOpencode(), toolchain=_FakeToolchain(), window=window,
        run_root=str(tmp_path / "runs"),
    )
    plan = tmp_path / "plan.md"
    plan.write_text("打印 ok", encoding="utf-8")
    window.left_pane.set_plan(str(plan))
    window.left_pane.blocking_combo.setCurrentText("error")

    with qtbot.waitSignal(controller.finished, timeout=15_000):
        controller.start()

    assert window.right_pane.blocking_level == "error"
    # 断言屏幕上那句话就是**按当前级别算出来**的那句（规则本身对不对由下面那条单元测试管）
    from tu_shell_agent.ui.panes.right import blocking_rule

    assert blocking_rule("error") in window.right_pane.findings_summary.text()


def test_right_pane_blocking_rule_matches_engine_semantics():
    """右栏那句"哪些会阻断"必须与引擎的判定逐级一致（四个级别全试）。

    原来它是一句写死的常量："error/warning/info 会阻断并回灌修复，style 只展示"。
    级别设成 error 时它仍宣称 info 会阻断，而同一屏正把某条 info 标成"仅展示"；
    级别设成 style 时它又说 style 只展示，而引擎（blocks_run）明明会回灌修复。
    """
    from tu_shell_agent.types import SEVERITY_RANK, blocks_run
    from tu_shell_agent.ui.panes.right import blocking_rule

    for level in SEVERITY_RANK:
        rule = blocking_rule(level)
        claimed = set(rule.split("（")[1].split("）")[0].split("/"))
        actual = {item for item in SEVERITY_RANK if blocks_run(item, level)}
        assert claimed == actual, f"{level}: {rule}"


def test_settings_values_reach_the_engine_config_and_the_right_pane(qtbot, tmp_path):
    """设置页那四个运行参数不能是死值：必须进左栏（= 进本次运行 config），
    而右栏"会不会阻断"的标注必须跟着**生效**的级别走。

    原实现里左栏把这四个值写死成引擎默认值、没有任何代码读 AppSettings，而右栏的级别
    只从设置读 —— 同一个旋钮两个来源：引擎按 info 阻断、报告按设置的 error 标注，
    用户会看到"这条只展示不触发修复"，而它恰恰就是把这次运行打进 needs_human 的那条。
    """
    window = MainWindow(
        wire_controller=False,
        settings=AppSettings(
            run_root=str(tmp_path / "runs"),
            templates_dir=str(tmp_path / "templates"),
            blocking_level="error",
            max_rounds=5,
            generate_timeout_ms=77_000,
            execute_timeout_ms=33_000,
        ),
    )
    qtbot.addWidget(window)
    controller = RunController(
        opencode=_FakeOpencode(), toolchain=_FakeToolchain(), window=window,
        run_root=str(tmp_path / "runs"),
    )
    plan = tmp_path / "plan.md"
    plan.write_text("打印 ok", encoding="utf-8")
    window.left_pane.set_plan(str(plan))

    config = window.left_pane.to_run_config()
    assert (config.blocking_level, config.max_rounds) == ("error", 5)
    assert (config.generate_timeout_ms, config.execute_timeout_ms) == (77_000, 33_000)

    with qtbot.waitSignal(controller.finished, timeout=15_000):
        controller.start()

    assert window.right_pane.blocking_level == "error"
    meta = json.loads((Path(controller._run_dir) / "meta.json").read_text(encoding="utf-8"))
    assert meta["config"]["blocking_level"] == "error"
    assert meta["config"]["max_rounds"] == 5


def test_finding_activation_jumps_the_script_view(qtbot, tmp_path):
    """右栏双击一条发现要跳到中栏对应行（规格 §12）：不接线那句话就是空头承诺。"""
    from tu_shell_agent.ui.run_controller import RunController

    window = _window(qtbot, tmp_path)
    RunController(
        opencode=_FakeOpencode(), toolchain=_FakeToolchain(), window=window,
        run_root=str(tmp_path / "runs"),
    )
    window.center_pane.show_round(1, "#!/usr/bin/env bash\necho one\necho two\necho three\n")

    window.right_pane.finding_activated.emit(3)

    assert window.center_pane.script_view.textCursor().blockNumber() == 2  # 第 3 行（0 基）


class _ContractFailingOpencode(_FakeOpencode):
    """交回来的脚本缺锚点 → 第 1 轮就契约失败，这一轮不会有 shellcheck/execute 事件。"""

    def generate(self, session_id, message, schema, timeout_ms, on_delta=None, cancel=None):
        return GeneratedScript(script="#!/usr/bin/env bash\necho 缺锚点\n", notes="n", assumptions=())


def test_second_run_does_not_leave_the_previous_run_on_screen(qtbot, tmp_path):
    """连跑两次时，第二次的屏幕上不能留下第一次的脚本/退出码/输出。

    第二次如果在生成阶段就失败（本机无凭据、opencode 起不来都是常态），本次不会有任何
    script/shellcheck/execute 事件；不主动清屏的话，屏幕上就是「结论：需要人工」+
    「退出码 0」+ 上一次的 stdout 并列 —— 最容易被读成"这次也成功了"。
    """
    window = _window(qtbot, tmp_path)
    controller = RunController(
        opencode=_FakeOpencode(), toolchain=_FakeToolchain(), window=window,
        run_root=str(tmp_path / "runs"),
    )
    plan = tmp_path / "plan.md"
    plan.write_text("打印 ok", encoding="utf-8")
    window.left_pane.set_plan(str(plan))

    with qtbot.waitSignal(controller.finished, timeout=15_000) as first:
        controller.start()
    assert first.args[0].outcome == "succeeded"
    assert window.right_pane.output_view.toPlainText().strip() == "ok"

    # 第二次：opencode 起不来 → 本轮**一个 script 事件都不会有**，
    # 所以清屏只能发生在开跑前（不能指望"新脚本到达时清"，那时压根没有新脚本）。
    controller._opencode = _StartFailsOpencode()
    with qtbot.waitSignal(controller.finished, timeout=15_000) as second:
        controller.start()

    assert second.args[0].outcome == "aborted_dependency"
    assert window.center_pane.current_text().strip() == ""               # 没有上一次的脚本
    assert window.right_pane.output_view.toPlainText().strip() == ""      # 没有上一次的输出
    assert "尚未" in window.right_pane.execute_summary.text()             # 没有上一次的退出码
    assert "0 处" not in window.right_pane.findings_summary.text()        # 没说"本轮没有发现"


def test_new_round_clears_the_previous_rounds_report(qtbot, tmp_path):
    """中间轮契约失败时，右栏不许把上一轮的退出码/stdout 摆在**本轮**脚本旁边。"""
    window = _window(qtbot, tmp_path)
    controller = RunController(
        opencode=_FakeOpencode(), toolchain=_FakeToolchain(), window=window,
        run_root=str(tmp_path / "runs"),
    )
    window.center_pane.show_round(1, "#!/usr/bin/env bash\necho 第一轮\n")
    window.right_pane.render_execute(
        ExecuteResult(0, None, False, False, 51, "第一轮的输出\n", "")
    )

    # 第二轮脚本到达（引擎先发 script 再校验契约）
    from tu_shell_agent.types import RunEvent

    controller._on_event(RunEvent("script", 2, {"script": "#!/usr/bin/env bash\necho 第二轮\n"}))

    assert "第二轮的输出" not in window.right_pane.output_view.toPlainText()
    assert "第一轮的输出" not in window.right_pane.output_view.toPlainText()
    assert "尚未" in window.right_pane.execute_summary.text()


def test_replay_restores_execute_summary_and_error_evidence(qtbot, tmp_path):
    """回放必须还原执行结论；失败运行必须能看到落盘的错误证据。"""
    window = _window(qtbot, tmp_path)
    RunController(
        opencode=_FakeOpencode(), toolchain=_FakeToolchain(), window=window,
        run_root=str(tmp_path / "runs"),
    )
    root = tmp_path / "runs"
    ok = root / "20260918-100000-aaaa"
    (ok / "attempts" / "1").mkdir(parents=True)
    (ok / "script.sh").write_text("echo replay\n", encoding="utf-8")
    (ok / "meta.json").write_text(
        json.dumps({"outcome": "succeeded", "rounds": 1, "config": {"blocking_level": "error"}}),
        encoding="utf-8",
    )
    (ok / "attempts" / "1" / "execute.json").write_text(
        json.dumps({"exit_code": 3, "timed_out": False, "cancelled": False, "duration_ms": 733}),
        encoding="utf-8",
    )
    (ok / "attempts" / "1" / "stdout.txt").write_text("replay out\n", encoding="utf-8")

    failed = root / "20260918-110000-bbbb"
    (failed / "attempts" / "1").mkdir(parents=True)
    (failed / "meta.json").write_text('{"outcome": "needs_human", "rounds": 1}', encoding="utf-8")
    (failed / "attempts" / "1" / "generation-error.txt").write_text(
        "opencode 返回错误 APIError：没有凭据\n", encoding="utf-8"
    )

    window.history_page.run_root = str(root)
    window.history_page.reload()
    rows = {
        window.history_page.list_widget.item(i).text(): i
        for i in range(window.history_page.list_widget.count())
    }

    window.history_page.list_widget.setCurrentRow(rows["20260918-100000-aaaa · succeeded · 1 轮"])
    assert "退出码 3" in window.right_pane.execute_summary.text()
    assert "733" in window.right_pane.execute_summary.text()
    assert window.right_pane.blocking_level == "error"   # 用**那次运行**记的级别标注

    window.history_page.list_widget.setCurrentRow(rows["20260918-110000-bbbb · needs_human · 1 轮"])
    assert "没有凭据" in window.right_pane.output_view.toPlainText()   # 失败原因看得见
    assert "尚未" in window.right_pane.execute_summary.text()          # 没跑就是没跑


def test_shutdown_waits_for_the_detect_worker(qtbot, tmp_path):
    """关窗时必须把探测线程也收掉。

    运行中的 QThread 被析构会让进程 abort（核心转储）；探测线程是启动自检与
    "重新检测"按钮都会起的（上限 20s/件 × 3 件），窗口关掉时它很可能还在跑。
    等不到就故意不回收（挂进 _ORPHANS 持有引用），但不能像没看见一样直接走人。
    """
    import time as _time

    from tu_shell_agent.ui import run_controller as rc
    from tu_shell_agent.ui.engine_worker import DetectWorker

    class SlowDetect(DetectWorker):
        def run(self) -> None:  # noqa: D102 - 探针：慢探测
            _time.sleep(0.6)

    window = _window(qtbot, tmp_path)
    controller = RunController(
        opencode=_FakeOpencode(), toolchain=_FakeToolchain(), window=window,
        run_root=str(tmp_path / "runs"),
    )
    monkeypatch_worker = SlowDetect({})
    controller._detect_worker = monkeypatch_worker
    monkeypatch_worker.start()
    assert monkeypatch_worker.isRunning()

    controller.shutdown()

    assert not monkeypatch_worker.isRunning(), "关窗后探测线程仍在运行"
    assert monkeypatch_worker not in rc._ORPHANS


def test_auto_confirm_without_a_preset_answer_approves(qtbot, tmp_path):
    """`auto_confirm=True` + `confirm_answer=None` 必须是"批准"，不是"拒绝"。

    `confirm_answer=None` 的语义是"没有预置答案"（生产装配就是
    auto_confirm=False + answer=None → 弹真实对话框）。若把 None 当成 False，
    "打开了自动确认但没给答案"会变成每次执行都被静默拒掉 —— 现场表现是
    运行结论永远是 cancelled、脚本一次都没跑，而报告里一切正常。
    """
    window = _window(qtbot, tmp_path)
    controller = RunController(
        opencode=_FakeOpencode(), toolchain=_FakeToolchain(), window=window,
        run_root=str(tmp_path / "runs"), auto_confirm=True, confirm_answer=None,
    )
    plan = tmp_path / "plan.md"
    plan.write_text("打印 ok", encoding="utf-8")
    window.left_pane.set_plan(str(plan))

    with qtbot.waitSignal(controller.finished, timeout=15_000) as blocker:
        controller.start()

    assert blocker.args[0].outcome == "succeeded"
    assert window.right_pane.output_view.toPlainText().strip() == "ok"


class _GenerateFailsOpencode(_FakeOpencode):
    """生成阶段就失败（本机无凭据时上游拒绝生成，就是这个形态）。"""

    def generate(self, session_id, message, schema, timeout_ms, on_delta=None, cancel=None):
        raise RuntimeError("opencode 返回错误 APIError：Error from provider (Console): 没有凭据")


def test_live_failure_shows_the_reason_on_screen(qtbot, tmp_path):
    """实时跑失败时，屏幕上必须有失败原因，而不是只有一句结论。

    原实现：失败后中栏空、右栏"尚未校验"、输出空，理由只躺在
    `attempts/<n>/generation-error.txt` 里 —— 用户只能去翻运行目录。
    """
    window = _window(qtbot, tmp_path)
    controller = RunController(
        opencode=_GenerateFailsOpencode(), toolchain=_FakeToolchain(), window=window,
        run_root=str(tmp_path / "runs"),
    )
    plan = tmp_path / "plan.md"
    plan.write_text("打印 ok", encoding="utf-8")
    window.left_pane.set_plan(str(plan))

    with qtbot.waitSignal(controller.finished, timeout=15_000) as blocker:
        controller.start()

    assert blocker.args[0].outcome == "needs_human"
    text = window.right_pane.output_view.toPlainText()
    assert "错误证据" in text
    assert "没有凭据" in text                     # 真正的失败原因
    assert "generation-error.txt" in text         # 并且标明它来自哪个文件
    assert "失败原因见下方输出区" in window.status_label.text()


def test_successful_run_is_not_clobbered_by_error_evidence(qtbot, tmp_path):
    """成功运行不许被"错误证据"覆盖输出区（只有失败才展示证据）。"""
    window = _window(qtbot, tmp_path)
    controller = RunController(
        opencode=_FakeOpencode(), toolchain=_FakeToolchain(), window=window,
        run_root=str(tmp_path / "runs"),
    )
    plan = tmp_path / "plan.md"
    plan.write_text("打印 ok", encoding="utf-8")
    window.left_pane.set_plan(str(plan))

    with qtbot.waitSignal(controller.finished, timeout=15_000):
        controller.start()

    assert window.right_pane.output_view.toPlainText().strip() == "ok"
    assert "执行结果：正常退出" in window.right_pane.execute_summary.text()


def test_verify_edited_keeps_the_edited_script_on_screen(qtbot, tmp_path):
    """改后重跑时，用户刚改的那个脚本必须留在中栏。

    实时路径开跑前会清屏（避免留下上一次运行的残留），但"这次要跑的脚本"不能一起被清掉：
    改后重跑不经过生成步骤，不会有 script 事件把它重新摆上来 —— 原实现里用户一点按钮，
    脚本就从眼前消失了，右栏却在报这份脚本的执行结果。
    """
    window = _window(qtbot, tmp_path)
    controller = RunController(
        opencode=_FakeOpencode(), toolchain=_FakeToolchain(), window=window,
        run_root=str(tmp_path / "runs"),
    )
    edited = "#!/usr/bin/env bash\necho 用户手改的脚本\n"
    controller.set_script_override(edited)

    with qtbot.waitSignal(controller.finished, timeout=15_000):
        controller.verify_edited()

    assert "用户手改的脚本" in window.center_pane.current_text()
    assert window.right_pane.output_view.toPlainText().strip() == "ok"


def test_continue_repair_shows_the_script_being_repaired(qtbot, tmp_path):
    """续跑时中栏要显示"正在修的那一份"，否则生成下一版的几十秒里用户不知道在等什么。"""
    window = _window(qtbot, tmp_path)
    controller = RunController(
        opencode=_FakeOpencode(), toolchain=_FakeToolchain(), window=window,
        run_root=str(tmp_path / "runs"),
    )
    run_dir = tmp_path / "runs" / "20260918-100000-aaaa"
    (run_dir / "attempts" / "2").mkdir(parents=True)
    (run_dir / "attempts" / "2" / "script.sh").write_text(
        "#!/usr/bin/env bash\necho 待修复的第二版\n", encoding="utf-8"
    )
    (run_dir / "meta.json").write_text(
        '{"outcome": "needs_human", "rounds": 2, "sessionId": "ses_x"}', encoding="utf-8"
    )
    controller._run_dir = str(run_dir)
    controller.window.left_pane.run_root_edit.setText(str(tmp_path / "runs"))

    # 在**第一个事件到达时**取样：那时已经过了"开跑前摆好脚本"，而引擎的 script 事件
    # 还没来（它要等生成完成）。看最终状态是测不出来的 —— 新版本会把它覆盖掉。
    sampled: list[str] = []

    def on_event(_event) -> None:
        if not sampled:
            sampled.append(window.center_pane.current_text())

    controller.events.connect(on_event)
    with qtbot.waitSignal(controller.finished, timeout=15_000):
        controller.continue_repair()

    assert sampled, "一个事件都没收到"
    assert "待修复的第二版" in sampled[0]


class _RecordingAdapter:
    """记录 resume/start 是否被调用（续跑必须走 resume：不新建会话但要起 serve）。"""

    def __init__(self) -> None:
        self.resumed: list[tuple[str, object]] = []
        self.disposed = 0

    def resume(self, run_dir, model=None):        # noqa: ANN001, ANN201
        self.resumed.append((run_dir, model))

    def start(self, run_dir, agent_name=None, model=None):   # noqa: ANN001, ANN201
        raise AssertionError("续跑不该新建会话")

    def dispose(self) -> None:
        self.disposed += 1


def test_continue_repair_resumes_the_adapter_instead_of_starting_it(qtbot, tmp_path):
    """续跑必须调用 `adapter.resume()`。

    原实现里 `resume_repair` 不经过 `run_loop` 的 `start()`，适配器从未启动，
    第一次 generate 就抛"适配器未启动"，被编排层当成契约失败吞掉 ——
    "继续修复"按钮在无头替身之外其实是坏的（白烧剩余轮次后收在 needs_human）。
    """
    window = _window(qtbot, tmp_path)
    controller = RunController(
        opencode=_FakeOpencode(), toolchain=_FakeToolchain(), window=window,
        run_root=str(tmp_path / "runs"),
    )
    run_dir = tmp_path / "runs" / "20260918-100000-aaaa"
    (run_dir / "attempts" / "1").mkdir(parents=True)
    (run_dir / "attempts" / "1" / "script.sh").write_text("echo 第一版\n", encoding="utf-8")
    (run_dir / "meta.json").write_text(
        '{"outcome": "needs_human", "rounds": 1, "sessionId": "ses_x"}', encoding="utf-8"
    )
    controller._run_dir = str(run_dir)
    adapter = _RecordingAdapter()
    controller._adapter = adapter          # 假装生产路径已经探测并造好了适配器
    controller._opencode = adapter
    controller._toolchain = _FakeToolchain()

    with qtbot.waitSignal(controller.finished, timeout=15_000):
        controller.continue_repair()

    assert adapter.resumed == [(str(run_dir), None)], "续跑没有调用 resume()"


# ── 模型对话（问它、让它解释）────────────────────────────────────────────


class _ChattyOpencode(_FakeOpencode):
    """支持自由对话的替身：记录收到的问题，回复里带一段脚本。"""

    def __init__(self) -> None:
        self.sessions: list[str] = []
        self.questions: list[tuple[str, str, str]] = []
        self.reply = "先备份再删除，脚本如下：\n\n```bash\necho 来自对话的脚本\n```\n"

    def start(self, run_dir, agent_name="tu-shell-agent", model=None):
        session = f"ses_chat_{len(self.sessions) + 1}"
        self.sessions.append(session)
        return session

    def chat(self, session_id, message, timeout_ms, on_delta=None, cancel=None, system_preamble=""):
        self.questions.append((session_id, message, system_preamble))
        if on_delta is not None:
            on_delta(self.reply[:5])
            on_delta(self.reply[5:])
        return self.reply


class _ChatOpencodeWithSession(_FakeOpencode):
    """已有一个运行会话（meta.json 里有 sessionId）时，对话必须复用它。"""

    def __init__(self) -> None:
        self.questions: list[tuple[str, str, str]] = []
        self.start_calls = 0

    def start(self, run_dir, agent_name="tu-shell-agent", model=None):
        self.start_calls += 1
        return "ses_不应该被调用"

    def chat(self, session_id, message, timeout_ms, on_delta=None, cancel=None, system_preamble=""):
        self.questions.append((session_id, message, system_preamble))
        return "好的。"


def test_ask_creates_a_session_and_streams_the_reply(qtbot, tmp_path):
    """第一次提问要建立会话，回复流式追加到记录区，并且带上方案上下文。"""
    window = _window(qtbot, tmp_path)
    opencode = _ChattyOpencode()
    controller = RunController(
        opencode=opencode, toolchain=_FakeToolchain(), window=window,
        run_root=str(tmp_path / "runs"),
    )
    plan = tmp_path / "plan.md"
    plan.write_text("把 .log 清掉，但别动 logs/ 目录", encoding="utf-8")
    window.left_pane.set_plan(str(plan))
    chat = window.chat_panel

    chat.input.setPlainText("为什么第一轮失败了？")
    chat.send_button.click()
    qtbot.waitUntil(lambda: chat.send_button.isEnabled(), timeout=10_000)

    text = chat.transcript_text()
    assert "你：为什么第一轮失败了？" in text
    assert "模型回复" in text
    assert "先备份再删除" in text                 # 流式增量落进了记录区
    assert opencode.sessions, "应当建立了一个对话会话"
    # 新会话的第一句话带上方案上下文：否则模型不知道这个项目在干什么
    assert "别动 logs/ 目录" in opencode.questions[0][2]


def test_ask_reuses_the_existing_run_session(qtbot, tmp_path):
    """已经有运行会话时，对话必须复用它（同一个上下文），而不是另起一个会话。"""
    window = _window(qtbot, tmp_path)
    opencode = _ChatOpencodeWithSession()
    controller = RunController(
        opencode=opencode, toolchain=_FakeToolchain(), window=window,
        run_root=str(tmp_path / "runs"),
    )
    run_dir = tmp_path / "runs" / "20260919-000000-aaaa"
    run_dir.mkdir(parents=True)
    (run_dir / "meta.json").write_text(
        '{"outcome": "needs_human", "rounds": 1, "sessionId": "ses_运行里的"}', encoding="utf-8"
    )
    controller._run_dir = str(run_dir)
    controller._session_id = "ses_运行里的"

    window.chat_panel.input.setPlainText("解释一下这条报告")
    window.chat_panel.send_button.click()
    qtbot.waitUntil(lambda: window.chat_panel.send_button.isEnabled(), timeout=10_000)

    assert opencode.start_calls == 0, "不该另起会话"
    assert opencode.questions[0][0] == "ses_运行里的"
    assert opencode.questions[0][2] == ""        # 复用会话时不重复灌上下文


def test_extract_script_puts_it_in_the_center_pane_without_running_it(qtbot, tmp_path):
    """「把最新脚本放进中栏」只放进中栏，不执行：执行仍要走改后重跑（shellcheck + 确认）。"""
    window = _window(qtbot, tmp_path)
    toolchain = _FakeToolchain()
    RunController(
        opencode=_ChattyOpencode(), toolchain=toolchain, window=window,
        run_root=str(tmp_path / "runs"),
    )
    chat = window.chat_panel
    chat.add_assistant("改好的版本：\n\n```bash\necho 来自对话的脚本\n```\n")

    chat.extract_button.click()

    assert "来自对话的脚本" in window.center_pane.current_text()
    assert toolchain.executed == 0, "对话里的脚本绝不能被自动执行"
    assert window.center_pane.tabs.currentIndex() == 0   # 切回「本轮」让用户看到它


def test_assistant_delta_streams_into_the_chat_transcript(qtbot, tmp_path):
    """运行期间模型的增量输出要落到对话记录里（原来只有状态栏一句"模型输出中…"）。"""
    from tu_shell_agent.types import RunEvent

    window = _window(qtbot, tmp_path)
    controller = RunController(
        opencode=_FakeOpencode(), toolchain=_FakeToolchain(), window=window,
        run_root=str(tmp_path / "runs"),
    )

    controller._on_event(RunEvent("assistant_delta", 1, {"text": "#!/usr/bin/env bash\n"}))
    controller._on_event(RunEvent("assistant_delta", 1, {"text": "echo hi\n"}))

    text = window.chat_panel.transcript_text()
    assert "第 1 轮 · 模型输出" in text
    assert "echo hi" in text


def test_extra_instruction_reaches_the_engine_prompt(qtbot, tmp_path):
    """左栏的补充要求要真的进提示词（进不了的话那个框就是摆设）。"""
    window = _window(qtbot, tmp_path)
    seen: list[str] = []

    class _Recording(_FakeOpencode):
        def generate(self, session_id, message, schema, timeout_ms, on_delta=None, cancel=None):
            seen.append(message)
            return super().generate(session_id, message, schema, timeout_ms, on_delta, cancel)

    controller = RunController(
        opencode=_Recording(), toolchain=_FakeToolchain(), window=window,
        run_root=str(tmp_path / "runs"),
    )
    plan = tmp_path / "plan.md"
    plan.write_text("清理日志", encoding="utf-8")
    window.left_pane.set_plan(str(plan))
    window.left_pane.extra_edit.setPlainText("这次别动 logs/ 目录")

    with qtbot.waitSignal(controller.finished, timeout=15_000):
        controller.start()

    assert "## 补充要求" in seen[0]
    assert "这次别动 logs/ 目录" in seen[0]
    # 也要作为冻结输入落盘，方便事后查"这次为什么这么改"
    assert (Path(controller._run_dir) / "extra.md").read_text(encoding="utf-8") == "这次别动 logs/ 目录"
