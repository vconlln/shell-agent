"""右栏的测试契约：报告分组与计数、点击跳行、执行摘要、模型取舍必须可见。

前 4 条来自计划任务 8 的清单（原文照抄）；后 4 条是计划没写、但产品语义要求的补齐：
`render_execute` 的三态（计划实现要点里写明要求，测试只覆盖了超时那一态）、
「注入的阻断级别」必须显示（计划标题与规格 §12 的要求）、以及 notes 缺失时不能留白
（规格 §11 把"取舍必须让人看见"定为本工具覆盖该风险的唯一手段，留白会被读成"没有取舍"）。
"""

from __future__ import annotations

import pytest

from tu_shell_agent.types import ExecuteResult, ShellcheckFinding
from tu_shell_agent.ui.panes.right import RightPane


def _pane(qtbot) -> RightPane:
    pane = RightPane()
    qtbot.addWidget(pane)
    return pane


def test_findings_are_grouped_by_code_with_counts(qtbot):
    pane = _pane(qtbot)
    pane.render_findings([
        ShellcheckFinding("SC2086", 5, 6, "info", "quote it"),
        ShellcheckFinding("SC2086", 9, 6, "info", "quote it again"),
        ShellcheckFinding("SC2045", 4, 10, "error", "use glob"),
    ])
    tree = pane.findings_tree
    assert tree.topLevelItemCount() == 2
    first = tree.topLevelItem(0)
    assert first.text(0).startswith("SC2086")
    assert "2" in first.text(1)          # 计数
    assert first.childCount() == 2


def test_activating_a_finding_emits_its_line(qtbot):
    pane = _pane(qtbot)
    pane.render_findings([ShellcheckFinding("SC2045", 4, 10, "error", "use glob")])
    lines: list[int] = []
    pane.finding_activated.connect(lines.append)

    item = pane.findings_tree.topLevelItem(0).child(0)
    pane.findings_tree.itemActivated.emit(item, 0)

    assert lines == [4]


def test_render_execute_shows_exit_code_duration_and_flags(qtbot):
    pane = _pane(qtbot)
    pane.render_execute(ExecuteResult(-9, None, True, False, 400, "out\n", "err\n"))
    assert "退出码" in pane.execute_summary.text()
    assert "-9" in pane.execute_summary.text()
    assert "超时" in pane.execute_summary.text()
    assert "out" in pane.output_view.toPlainText()
    assert "err" in pane.output_view.toPlainText()


def test_render_notes_shows_tradeoffs_and_assumptions(qtbot):
    """规格 §11 硬性要求：模型对方案约束的取舍必须与脚本并列可见。"""
    pane = _pane(qtbot)
    pane.render_notes("把'目录不存在即失败'改成报 0 个文件", ("运行目录里没有 logs/",))
    text = pane.notes_view.toPlainText()
    assert "报 0 个文件" in text
    assert "没有 logs/" in text


def test_report_shows_injected_blocking_level_and_counts_per_level(qtbot):
    """阻断级别是"注入"的（本次运行参数），报告必须说清是按哪一级判的。"""
    pane = _pane(qtbot)
    pane.render_findings([
        ShellcheckFinding("SC2086", 5, 6, "info", "quote it"),
        ShellcheckFinding("SC2045", 4, 10, "error", "use glob"),
        ShellcheckFinding("SC2034", 7, 1, "warning", "unused"),
        ShellcheckFinding("SC2001", 8, 1, "style", "sed"),
    ])
    summary = pane.findings_summary.text()
    assert "阻断级别 info" in summary
    assert "error 1" in summary
    assert "warning 1" in summary
    assert "info 1" in summary
    assert "style 1" in summary


def test_blocking_marker_follows_the_injected_level(qtbot):
    pane = _pane(qtbot)
    pane.blocking_level = "error"
    pane.render_findings([
        ShellcheckFinding("SC2086", 5, 6, "info", "quote it"),
        ShellcheckFinding("SC2045", 4, 10, "error", "use glob"),
    ])
    info_group, error_group = pane.findings_tree.topLevelItem(0), pane.findings_tree.topLevelItem(1)
    assert "阻断" not in info_group.text(0)      # error 级别下 info 不阻断，只展示
    assert "阻断" in error_group.text(0)

    pane.blocking_level = "info"                 # 换级别后同一份报告结论不同，必须重画
    assert "阻断" in pane.findings_tree.topLevelItem(0).text(0)
    assert "阻断级别 info" in pane.findings_summary.text()


@pytest.mark.parametrize(
    ("result", "expected"),
    [
        (ExecuteResult(0, None, False, False, 120, "ok\n", ""), "正常退出"),
        (ExecuteResult(3, None, False, False, 120, "", "boom\n"), "非零退出"),
        (ExecuteResult(-9, None, True, False, 400, "", ""), "超时"),
        (ExecuteResult(None, None, False, True, 30, "", ""), "已取消"),
    ],
)
def test_execute_summary_covers_every_end_state(qtbot, result: ExecuteResult, expected: str):
    pane = _pane(qtbot)
    pane.render_execute(result)
    text = pane.execute_summary.text()
    assert expected in text
    assert "退出码" in text
    if expected != "超时":
        assert "超时" not in text


def test_absent_notes_are_stated_and_succeeded_is_not_oversold(qtbot):
    pane = _pane(qtbot)
    assert "不代表" in pane.notes_header.text()   # 规格 §11：succeeded ≠ 方案被正确实现

    pane.render_notes("", ())
    text = pane.notes_view.toPlainText()
    assert "未给出" in text and "未声明" in text  # 留白会被读成"没有取舍"


def test_empty_report_is_not_the_same_as_never_checked(qtbot):
    """`render_findings([])` = 校验过、这轮没发现；与"还没校验过"是两回事，不能同一句话。"""
    pane = _pane(qtbot)
    assert "尚未校验" in pane.findings_summary.text()

    pane.render_findings([])
    summary = pane.findings_summary.text()
    assert "0 处" in summary
    assert "尚未校验" not in summary
