"""高 DPI / 小逻辑分辨率下的布局：不许重叠、不许出框、不许把面板压没。

用户反馈（Windows，带系统缩放）三条症状：
  1. 左栏文字出框（"运行根目录"被截尾）；
  2. 底部的「模型对话」与输入框重叠，按钮行也被压；
  3. 右侧「校验与输出」的框被压缩。

根因不是"Windows 特有"：窗口最小尺寸写死了 960x780，而 Windows 150% 缩放下 1080p 只有
720 逻辑像素高 —— **最小尺寸比屏幕还高**，窗口管理器照给，布局只能违反最小尺寸，于是重叠、
出框、压缩全都来了。另外 QSS 给 QLabel 的 padding 不计入 sizeHint，表单标签列每种缩放都窄 4px。
"""

from __future__ import annotations

from PySide6.QtCore import QRect
from PySide6.QtGui import QFontMetrics
from PySide6.QtWidgets import QFormLayout, QLabel

from tu_shell_agent.ui.main_window import MainWindow, window_minimum_for
from tu_shell_agent.ui.settings import AppSettings


def _window(qtbot, **fields) -> MainWindow:
    settings = AppSettings(**fields)
    window = MainWindow(wire_controller=False, settings=settings)
    qtbot.addWidget(window)
    return window


def _overlaps(first: QRect, second: QRect) -> bool:
    inter = first.intersected(second)
    return inter.width() > 2 and inter.height() > 2


# ── 窗口最小尺寸：必须落在屏幕之内，同时保留可用下限 ────────────────────


def test_window_minimum_never_exceeds_the_screen():
    """窗口最小尺寸按屏幕可用区收敛 —— 这是三条症状的共同根因。

    1080p 在 150% 缩放下只有 1280x720 逻辑像素：写死 780 高就等于"最小尺寸比屏幕还高"。
    """
    for available in ((1280, 720), (1366, 768), (1440, 900), (1920, 1080), (2560, 1440)):
        width, height = window_minimum_for(available)
        assert width <= available[0], f"{available} 下最小宽度 {width} 超出屏幕"
        assert height <= available[1], f"{available} 下最小高度 {height} 超出屏幕"


def test_window_minimum_keeps_a_usable_floor():
    """再小的屏幕也不能把最小尺寸压到"没法用"：界面本身有下限。"""
    for available in ((800, 600), (640, 480), (1024, 600)):
        width, height = window_minimum_for(available)
        assert width >= 720 and height >= 520, f"{available} 下最小尺寸被压得不可用：{width}x{height}"


def test_window_minimum_grows_with_big_screens_but_is_capped():
    """大屏上取到"舒服的值"就够，不必无限放大（窗口该由用户自己拉）。"""
    assert window_minimum_for((3840, 2160)) == (960, 780)


# ── 对话面板：不许重叠 ────────────────────────────────────────────────


def test_chat_panel_never_overlaps_at_small_logical_sizes(qtbot):
    """小逻辑分辨率下，对话记录 / 输入框 / 按钮行不许互相重叠。

    实测（修前）：1440x900 就重叠 47px —— 因为记录 120 + 输入 72 把面板顶到 324，
    窗口地板被抬到 666，一旦屏幕放不下就开始违反最小尺寸。
    """
    for width, height in ((1440, 900), (1280, 720), (1100, 640), (960, 600)):
        window = _window(qtbot, ui_scale=0.8)
        window.resize(width, height)
        window.show()
        window.tool_tabs.setCurrentWidget(window.chat_panel)
        window.apply_appearance()

        chat = window.chat_panel
        transcript, input_box = chat.transcript.geometry(), chat.input.geometry()
        assert not _overlaps(transcript, input_box), f"{width}x{height} 下对话记录与输入框重叠"
        assert not _overlaps(input_box, chat.send_button.geometry()), (
            f"{width}x{height} 下输入框与按钮行重叠"
        )
        assert chat.transcript.height() >= chat.transcript.minimumHeight()
        window.close()


# ── 表单标签：不许出框 ────────────────────────────────────────────────


def test_form_labels_fit_after_every_scale(qtbot):
    """每个缩放档位下，表单标签都要放得下自己的文字。

    实测（修前）：每种缩放都差 4px —— `QLabel.sizeHint()` 不含 QSS 的 padding，
    而 QFormLayout 正是按 sizeHint 分配标签列宽度的。
    """
    for scale in (0.8, 1.0, 1.4):
        window = _window(qtbot, ui_scale=scale)
        window.resize(1500, 950)
        window.show()
        window.apply_appearance()

        checked = 0
        for form in window.left_pane.findChildren(QFormLayout):
            for row in range(form.rowCount()):
                item = form.itemAt(row, QFormLayout.ItemRole.LabelRole)
                label = item.widget() if item is not None else None
                if not isinstance(label, QLabel) or not label.text():
                    continue
                need = QFontMetrics(label.font()).horizontalAdvance(label.text())
                assert label.width() >= need, (
                    f"scale={scale} 时标签「{label.text()}」需要 {need}px，实得 {label.width()}px"
                )
                checked += 1
        assert checked > 0, "没有检查到任何表单标签（用例失效了）"
        window.close()


# ── 右侧栏：不许把面板压没 ────────────────────────────────────────────


def test_right_pane_sections_stay_usable_when_the_window_is_short(qtbot):
    """窗口很矮时，右栏三个面板仍各自保留可用高度（修前它们的最小高度合计 488）。"""
    window = _window(qtbot, ui_scale=0.8)
    window.resize(1100, 640)
    window.show()
    window.apply_appearance()

    pane = window.right_pane
    for name, widget in (
        ("校验报告", pane.findings_tree),
        ("执行输出", pane.output_view),
        ("取舍说明", pane.notes_view),
    ):
        assert widget.height() >= 40, f"{name} 在 1100x640 下只剩 {widget.height()}px"
    window.close()


# ── 工具区：默认收起（把高度还给脚本） ────────────────────────────────


def test_tool_area_starts_collapsed_and_expands_on_demand(qtbot):
    """底部工具区默认只留一行标题，点页签/标题时展开。

    用户反馈："工具区有点太占用空间了，软件的主要作用是写 shell 脚本"。
    """
    window = _window(qtbot, ui_scale=0.8)
    window.resize(1440, 900)
    window.show()
    window.apply_appearance()
    for _ in range(3):
        qtbot.wait(10)

    assert window.tool_section.is_collapsed(), "工具区默认应当是收起的"
    collapsed_height = window.tool_section.height()
    assert collapsed_height <= 60, f"收起后仍占 {collapsed_height}px"
    script_height_collapsed = window.center_pane.script_view.height()

    # 选中某个工具页 → 自动展开（东西在那儿就得看得见）
    window.tool_tabs.setCurrentWidget(window.chat_panel)
    for _ in range(3):
        qtbot.wait(10)
    assert not window.tool_section.is_collapsed()
    assert window.tool_section.height() >= 200, f"展开后只有 {window.tool_section.height()}px"
    # 展开的代价是脚本视图变矮 —— 收起来就该把这段高度还回来
    assert window.center_pane.script_view.height() < script_height_collapsed

    # 点标题能收回去
    window._on_tool_header_clicked(None)
    for _ in range(3):
        qtbot.wait(10)
    assert window.tool_section.is_collapsed()
    assert window.center_pane.script_view.height() == script_height_collapsed
    window.close()


def test_tool_area_state_is_remembered(qtbot, tmp_path):
    """收起/展开要记进设置：用户收起过，下次开窗不该又变回一大块。"""
    from tu_shell_agent.ui.settings import AppSettings

    settings = AppSettings(run_root=str(tmp_path / "runs"), templates_dir=str(tmp_path / "tpl"))
    window = MainWindow(wire_controller=False, settings=settings)
    qtbot.addWidget(window)
    window.resize(1440, 900)
    window.show()
    window.apply_appearance()
    assert settings.tool_area_collapsed is True

    window.tool_tabs.setCurrentWidget(window.chat_panel)      # 点页签 → 展开并记录
    for _ in range(3):
        qtbot.wait(10)
    assert settings.tool_area_collapsed is False
    window.close()
