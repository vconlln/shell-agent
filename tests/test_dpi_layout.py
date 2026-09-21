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


# ── 工具区：搬进「控制台」弹窗，主窗口高度全给脚本与对话 ──────────────


def test_tool_area_leaves_the_main_window_to_the_console_dialog(qtbot):
    """主窗口里不再有常驻工具区：工具页签收进「控制台」弹窗，脚本正文拿到整栏高度。

    用户反馈："工具区有点太占用空间了，软件的主要作用是写 shell 脚本" +
    "工具区可以放到底部，'开始、取消、继续修复'这里作成一个按钮，点开工具区就出现弹窗"。
    """
    window = _window(qtbot, ui_scale=0.8)
    window.resize(1440, 900)
    window.show()
    window.apply_appearance()
    for _ in range(3):
        qtbot.wait(10)

    assert not window.console_dialog.isVisible(), "控制台默认不该自己弹出来"
    assert not hasattr(window, "tool_section"), "主窗口里不该再有常驻工具区"
    # 中栏整高只分给"脚本正文 / 轮次时间线"两块，工具区不再从里面切走一条；
    # 脚本正文占大头就是"把高度还给脚本"的量化说法。
    pane_height = window.center_pane.height()
    script_height = window.center_pane.script_view.height()
    assert script_height >= pane_height * 0.6, (
        f"脚本正文只拿到 {script_height}px / 中栏 {pane_height}px"
    )
    script_height_without_console = script_height

    window.open_console(window.run_page)      # 底栏那个按钮 → 弹窗出现且停在「运行」页
    for _ in range(3):
        qtbot.wait(10)
    assert window.console_dialog.isVisible(), "点了控制台按钮弹窗没出来"
    assert window.tool_tabs.currentWidget() is window.run_page, "弹窗没停在请求的那一页"
    # 运行操作都在弹窗里，不再占主窗口
    assert window.start_button.isVisible()
    assert window.center_pane.script_view.height() == script_height_without_console, (
        "弹窗不该改变主窗口里脚本正文的高度"
    )
    window.console_close_button.click()
    for _ in range(3):
        qtbot.wait(10)
    assert not window.console_dialog.isVisible(), "关闭按钮没关掉弹窗"
    window.close()


def test_chat_is_a_page_of_the_right_column(qtbot):
    """模型对话在右栏的分页里（用户要求"模型对话放到右边"），且和校验输出同栏可切换。"""
    window = _window(qtbot, ui_scale=0.8)
    window.resize(1440, 900)
    window.show()
    window.apply_appearance()
    for _ in range(3):
        qtbot.wait(10)

    assert window.right_tabs.count() == 2
    assert window.right_tabs.tabText(0) == "模型对话"
    assert window.right_tabs.widget(0) is window.chat_panel
    assert window.right_tabs.widget(1) is window.right_pane
    window.close()
