"""中栏的测试契约：带行号的脚本视图、与上一轮的 diff、轮次时间线。

`diff-added` / `diff-removed` 两个 class 与 `CenterPane` 的公开方法是后续任务
（右栏点击跳转、事件驱动更新）依赖的契约，不要改名。
"""

from tu_shell_agent.ui.panes.center import CenterPane
from tu_shell_agent.ui.widgets.diff_view import render_diff_html
from tu_shell_agent.ui.widgets.script_view import ScriptView


def test_script_view_has_line_numbers_and_jump(qtbot):
    view = ScriptView()
    qtbot.addWidget(view)
    view.set_text("line1\nline2\nline3")     # 注意不留尾随换行：QPlainTextEdit 会为它多算一个空块
    assert view.blockCount() == 3
    assert view.line_number_area_width() > 0

    view.jump_to_line(3)
    assert view.textCursor().blockNumber() == 2  # 0-based


def test_render_diff_marks_added_and_removed_lines():
    html = render_diff_html("echo a\necho b\n", "echo a\necho B\n")
    assert "echo b" in html and "echo B" in html
    assert "diff-removed" in html and "diff-added" in html


def test_center_pane_shows_round_and_diff_toggle(qtbot):
    pane = CenterPane()
    qtbot.addWidget(pane)
    pane.show_round(1, "echo one\n")
    pane.show_round(2, "echo two\n")

    assert "echo two" in pane.current_text()
    pane.set_compare_with_previous(True)
    assert "echo one" in pane.compare_html() and "echo two" in pane.compare_html()


def test_center_pane_timeline_lists_rounds(qtbot):
    pane = CenterPane()
    qtbot.addWidget(pane)
    pane.add_timeline_entry(1, phase="checking", outcome="SC2045 ×1")
    pane.add_timeline_entry(2, phase="execute", outcome="退出码 0")
    texts = [pane.timeline.item(i).text() for i in range(pane.timeline.count())]
    assert texts[0].startswith("第 1 轮")
    assert "退出码 0" in texts[1]


def test_center_pane_reset_clears_rounds_and_timeline(qtbot):
    pane = CenterPane()
    qtbot.addWidget(pane)
    pane.show_round(1, "echo one\n")
    pane.show_round(2, "echo two\n")
    pane.set_compare_with_previous(True)
    pane.add_timeline_entry(1, phase="checking", outcome="SC2045 ×1")

    pane.reset()

    # 历史回放换一次运行前必须回到初始态：否则上一次运行的轮次会被当成新运行的
    # 「上一轮」，对比页给出的是两次运行之间的假差异
    assert pane.current_text() == ""
    assert pane.timeline.count() == 0
    assert "还没有脚本可对比" in pane.compare_html()
    assert pane.tabs.currentIndex() == 0
