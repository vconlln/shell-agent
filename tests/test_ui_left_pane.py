"""左栏的测试契约：选方案 + 预览 + 方案文件夹文件树，不发起运行。

**运行参数已经不在左栏**（用户 2026-09-20 睡前："请把左下角的运行参数放到设置里面，
其实设置已经有了，所以这里没有必要出现，把这部分替换成……类似于 vscode 的左侧文件树"）。
所以本文件里有两类用例：

1. 方案选择/预览/校验的行为（原来就有）；
2. "运行参数不许回来" —— 直接断言那几个控件在左栏里根本不存在。这类断言看着像在
   测"没有东西"，但它是这次改动的**唯一**契约：只要有人把表单加回来，两处能改同一件事
   的老问题就复活了，而且界面上不会有任何报错。

objectName（planEdit / planPreview / planTree / planTreeBox）是 RunController 与后续
审查者共同依赖的契约，不要改名。
"""

from pathlib import Path

import pytest

from tu_shell_agent.ui.panes.left import LeftPane

# 曾经出现在左栏「运行参数（仅本次）」里的控件名：现在只允许出现在设置页
RUN_PARAMETER_NAMES = (
    "runRootEdit",
    "blockingCombo",
    "maxRoundsSpin",
    "generateTimeoutSpin",
    "executeTimeoutSpin",
)


def test_plan_picker_loads_preview(qtbot, tmp_path: Path):
    plan = tmp_path / "plan.md"
    plan.write_text("# 方案：整理日志\n按 mtime 倒序列出 .log", encoding="utf-8")
    pane = LeftPane()
    qtbot.addWidget(pane)

    pane.set_plan(str(plan))

    assert "按 mtime 倒序" in pane.plan_preview.toPlainText()
    assert pane.plan_path() == str(plan)


def test_run_parameters_are_gone_from_left_pane(qtbot):
    """运行参数不许在左栏出现：它们与设置页的「运行」一节重复。

    **为什么值得一条用例**：删掉表单本身很容易，难的是防止它被"顺手加回来"。
    左栏多一个 runRootEdit，用户就会有两处能改运行根目录，而且改哪一处生效取决于
    读的是设置还是控件 —— 这种分歧不会报错，只会让人怀疑"我改了怎么没用"。
    """
    pane = LeftPane()
    qtbot.addWidget(pane)

    from PySide6.QtWidgets import QWidget

    existing = {child.objectName() for child in pane.findChildren(QWidget)}
    still_here = [name for name in RUN_PARAMETER_NAMES if name in existing]
    assert still_here == [], f"左栏又出现了运行参数控件：{still_here}"

    # 方法层面也不许留：`to_run_config()` 曾经是左栏的第二套配置来源
    assert not hasattr(pane, "to_run_config"), "左栏不该再有 to_run_config()"


def test_left_pane_has_the_file_tree_instead(qtbot, tmp_path: Path):
    """空出来的位置换成文件树（用户要的"vscode 左侧文件树那种效果"）。"""
    pane = LeftPane()
    qtbot.addWidget(pane)

    assert pane.plan_tree.objectName() == "planTreeBox"
    assert pane.plan_tree.view.objectName() == "planTree"
    # 树是左栏的子控件（挂在滚动区里），不是凭空建的一个独立窗口
    assert pane.plan_tree.parent() is not None

    plan = tmp_path / "方案.md"
    plan.write_text("# 方案", encoding="utf-8")
    pane.set_plan(str(plan))
    # 选方案后树里要跟着记住它：否则点「回到方案目录」时不知道回到哪
    assert pane.plan_tree.plan_path() == str(plan)


def test_choose_in_tree_sets_the_plan(qtbot, tmp_path: Path):
    """在树里点一个 .md → 就是选它当方案（路径、预览、信号一起动）。"""
    plan = tmp_path / "树里选的.md"
    plan.write_text("# 树里选的\n\n正文", encoding="utf-8")
    pane = LeftPane()
    qtbot.addWidget(pane)

    with qtbot.waitSignal(pane.plan_changed, timeout=2_000) as blocker:
        pane.plan_tree.plan_chosen.emit(str(plan))

    assert blocker.args[0] == str(plan)
    assert pane.plan_path() == str(plan)
    assert "树里选的" in pane.plan_preview.toPlainText()


def test_tree_root_change_is_forwarded_for_persistence(qtbot, tmp_path: Path):
    """树根目录换了要发出去（主窗口记进设置，下次打开还在原处）。"""
    pane = LeftPane()
    qtbot.addWidget(pane)
    target = tmp_path / "方案夹"
    target.mkdir()

    with qtbot.waitSignal(pane.plan_tree_root_changed, timeout=2_000) as blocker:
        assert pane.set_plan_tree_root(str(target)) is True

    assert blocker.args[0] == str(target)
    assert pane.plan_tree_root() == str(target)


def test_tree_root_rejects_a_missing_directory(qtbot, tmp_path: Path):
    """根目录不存在时返回 False，调用方可以退回默认值（静默换成空树最糟）。"""
    pane = LeftPane()
    qtbot.addWidget(pane)

    assert pane.set_plan_tree_root(str(tmp_path / "根本没有这个目录")) is False


def test_validate_reports_missing_inputs(qtbot):
    """没选方案必须拦下；**运行根不在这里报**（它归设置页管）。"""
    pane = LeftPane()
    qtbot.addWidget(pane)

    problems = pane.validate()

    assert any("方案" in problem for problem in problems)
    assert not any("运行根" in problem for problem in problems), problems


def test_validate_passes_for_ready_inputs(qtbot, tmp_path: Path):
    plan = tmp_path / "plan.md"
    plan.write_text("方案", encoding="utf-8")
    pane = LeftPane()
    qtbot.addWidget(pane)
    pane.set_plan(str(plan))

    assert pane.validate() == []


def test_widget_names_and_defaults_are_contract(qtbot):
    """控件名即契约：RunController 与主窗口按名字取控件，改名字会静默失效。"""
    pane = LeftPane()
    qtbot.addWidget(pane)

    assert pane.objectName() == "leftPane"
    assert pane.plan_edit.objectName() == "planEdit"
    assert pane.plan_preview.objectName() == "planPreview"
    assert pane.plan_edit.isReadOnly() is True
    assert pane.plan_preview.isReadOnly() is True
    assert pane.plan_path() == "" and pane.plan_text() == ""


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

        problems = pane.validate()
        assert any("方案" in problem and "空" in problem for problem in problems), problems


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


def test_extra_instruction_is_empty_by_default_and_passed_through(qtbot):
    """追加要求默认留空（空串时提示词里不该多出一个空段落）。"""
    pane = LeftPane()
    qtbot.addWidget(pane)

    assert pane.extra_instruction() == ""
    pane.extra_edit.setPlainText("只改日志轮转那一段")
    assert pane.extra_instruction() == "只改日志轮转那一段"


@pytest.mark.parametrize("suffix", [".md", ".markdown", ".txt"])
def test_plan_suffixes_are_the_ones_the_picker_offers(qtbot, suffix):
    """文件树里"点一下就当选方案"的后缀，必须与「选择方案…」对话框一致。

    两处不一致的后果是：对话框里能选的 .markdown，在树里点了却没反应。
    """
    from tu_shell_agent.ui.widgets.plan_tree import PLAN_SUFFIXES

    assert suffix in PLAN_SUFFIXES
