"""左栏的测试契约：选方案 + 填参数 + 预览，不发起运行。

objectName（planEdit / planPreview / runRootEdit / blockingCombo / maxRoundsSpin /
generateTimeoutSpin / executeTimeoutSpin）与默认值（阻断级别 info、3 轮、
300s/120s 超时）是任务 9 的 RunController 与后续审查者共同依赖的契约，不要改名改默认。
"""

from pathlib import Path

from tu_shell_agent.ui.panes.left import LeftPane


def test_plan_picker_loads_preview(qtbot, tmp_path: Path):
    plan = tmp_path / "plan.md"
    plan.write_text("# 方案：整理日志\n按 mtime 倒序列出 .log", encoding="utf-8")
    pane = LeftPane()
    qtbot.addWidget(pane)

    pane.set_plan(str(plan))

    assert "按 mtime 倒序" in pane.plan_preview.toPlainText()
    assert pane.plan_path() == str(plan)


def test_to_run_config_reflects_widgets(qtbot, tmp_path: Path):
    pane = LeftPane()
    qtbot.addWidget(pane)
    pane.run_root_edit.setText(str(tmp_path / "runs"))
    pane.blocking_combo.setCurrentText("warning")
    pane.max_rounds_spin.setValue(5)

    config = pane.to_run_config()

    assert config.run_root == str(tmp_path / "runs")
    assert config.blocking_level == "warning"
    assert config.max_rounds == 5


def test_validate_reports_missing_inputs(qtbot, tmp_path: Path):
    pane = LeftPane()
    qtbot.addWidget(pane)
    problems = pane.validate()
    assert any("方案" in problem for problem in problems)
    assert any("运行根" in problem for problem in problems)


def test_validate_passes_for_ready_inputs(qtbot, tmp_path: Path):
    plan = tmp_path / "plan.md"
    plan.write_text("方案", encoding="utf-8")
    pane = LeftPane()
    qtbot.addWidget(pane)
    pane.set_plan(str(plan))
    pane.run_root_edit.setText(str(tmp_path / "runs"))
    assert pane.validate() == []


def test_widget_names_and_defaults_are_contract(qtbot):
    """控件名与默认值即契约：RunController 按名字取控件，放错默认值会静默改变引擎行为。"""
    pane = LeftPane()
    qtbot.addWidget(pane)

    assert pane.objectName() == "leftPane"
    assert pane.plan_edit.objectName() == "planEdit"
    assert pane.plan_preview.objectName() == "planPreview"
    assert pane.run_root_edit.objectName() == "runRootEdit"
    assert pane.blocking_combo.objectName() == "blockingCombo"
    assert pane.max_rounds_spin.objectName() == "maxRoundsSpin"
    assert pane.generate_timeout_spin.objectName() == "generateTimeoutSpin"
    assert pane.execute_timeout_spin.objectName() == "executeTimeoutSpin"

    assert pane.plan_edit.isReadOnly() is True
    assert pane.plan_preview.isReadOnly() is True
    assert pane.plan_path() == "" and pane.plan_text() == ""
    assert [pane.blocking_combo.itemText(i) for i in range(pane.blocking_combo.count())] == [
        "error", "warning", "info", "style",
    ]
    assert pane.blocking_combo.currentText() == "info"   # 与 RunConfig 默认一致（SC2086 就是 info 级）
    assert pane.max_rounds_spin.value() == 3
    assert (pane.max_rounds_spin.minimum(), pane.max_rounds_spin.maximum()) == (1, 10)
    assert pane.generate_timeout_spin.value() == 300_000
    assert pane.execute_timeout_spin.value() == 120_000


def test_to_run_config_leaves_component_paths_to_settings(qtbot, tmp_path: Path):
    """组件路径只出现在设置页；左栏填了就等于两处可改，所以这里必须留空。"""
    pane = LeftPane()
    qtbot.addWidget(pane)
    pane.run_root_edit.setText(str(tmp_path / "runs"))
    pane.generate_timeout_spin.setValue(60_000)

    config = pane.to_run_config()

    assert config.generate_timeout_ms == 60_000
    assert (config.bash_path, config.shellcheck_path, config.opencode_path) == (None, None, None)


def test_set_plan_emits_signal_and_reports_unreadable_file(qtbot, tmp_path: Path):
    """拖入/选中的文件读不了时，预览里要如实写错误，并把路径问题交给 validate()。"""
    pane = LeftPane()
    qtbot.addWidget(pane)
    missing = tmp_path / "不存在的方案.md"

    with qtbot.waitSignal(pane.plan_changed, timeout=1_000) as blocker:
        pane.set_plan(str(missing))

    assert blocker.args[0] == str(missing)
    assert "读不到" in pane.plan_preview.toPlainText()
    assert any("方案" in problem for problem in pane.validate())


def test_empty_plan_is_rejected(qtbot, tmp_path):
    """0 字节/纯空白的方案必须被拦下。

    引擎对方案不做任何检查：空正文会被写进 run_dir/plan.md，模型照着"空方案"生成，
    最后还可能落成 succeeded —— 一次根本没有方案的运行被记成成功。
    """
    for name, content in (("empty.md", ""), ("blank.md", "   \n\n\t\n")):
        plan = tmp_path / name
        plan.write_text(content, encoding="utf-8")
        pane = LeftPane()
        qtbot.addWidget(pane)
        pane.set_plan(str(plan))
        pane.run_root_edit.setText(str(tmp_path / "runs"))

        problems = pane.validate()
        assert any("方案" in problem and "空" in problem for problem in problems), problems


def test_run_root_is_expanded_and_absolute(qtbot, tmp_path, monkeypatch):
    """`~/runs` 与相对路径都必须落成绝对路径。

    不处理的话引擎会把 "~/runs" 当成**相对**路径，在进程 CWD 下建一个名字就叫 `~`
    的目录，整次运行都落在那里，而界面毫无提示。
    """
    pane = LeftPane()
    qtbot.addWidget(pane)
    pane.run_root_edit.setText("~/tu-runs-review")

    assert pane.to_run_config().run_root == str(Path.home() / "tu-runs-review")

    pane.run_root_edit.setText("relative-runs")
    configured = pane.to_run_config().run_root
    assert Path(configured).is_absolute()
    assert configured == str(Path.cwd() / "relative-runs")


def test_non_utf8_plan_says_what_to_do(qtbot, tmp_path):
    """GBK/UTF-16 的方案文档要提示"另存为 UTF-8"，而不是笼统说"读不到"。

    目标用户是 Windows + 中文方案：记事本"另存为 ANSI/Unicode"是最常见的来源，
    按"读不到"去查权限是白费功夫。
    """
    plan = tmp_path / "gbk.md"
    plan.write_bytes("方案：按 mtime 倒序".encode("gbk"))
    pane = LeftPane()
    qtbot.addWidget(pane)
    pane.set_plan(str(plan))

    problems = pane.validate()
    assert any("UTF-8" in problem for problem in problems), problems
