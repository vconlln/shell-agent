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


def test_window_minimum_keeps_a_usable_floor_when_the_screen_allows():
    """屏幕装得下时保留 720x520 的可用下限；装不下时下限**让位给屏幕**。

    这两条要求在 640x480 这种屏幕上直接冲突（屏幕放不下 720x520），必须选一条：
    选"不超过屏幕"。理由是用户实测的那三条症状 —— 最小尺寸比屏幕还大时，窗口管理器照给，
    布局只能违反最小尺寸（文字出框 / 面板与输入框重叠 / 某栏被压没）；
    反过来（窗口比屏幕小）只是界面挤一点，不会互相覆盖。
    """
    for available in ((800, 600), (1024, 600), (1280, 720)):
        width, height = window_minimum_for(available)
        assert width >= 720 and height >= 520, f"{available} 下最小尺寸被压得不可用：{width}x{height}"

    # 屏幕比地板还小：收敛到屏幕的九五成（旧公式在这里给出 720x520，比 640x480 的屏幕还大）
    for available in ((640, 480), (533, 533)):
        width, height = window_minimum_for(available)
        assert width <= available[0] and height <= available[1], (
            f"{available} 下最小尺寸 {width}x{height} 超过了屏幕"
        )
        assert width >= int(available[0] * 0.9), f"{available} 下窗口被压得太小：{width}"


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


def test_run_buttons_live_in_the_bottom_bar_next_to_the_console(qtbot):
    """运行操作在主窗口底栏那一行，最右是「控制台」；控制台弹窗只放低频面板。

    两次裁定的合并结果：工具区页签搬进「控制台」弹窗（把高度还给脚本），
    但**运行部分的按钮必须在主界面**（用户："把控制台里面运行部分的按钮放到主界面和
    控制台同一行，控制台按钮往右边挪一挪"）—— 弹窗里那一页大半是空的，操作按钮藏进去
    等于每次开跑都要多点一次。
    """
    from PySide6.QtCore import QPoint

    window = _window(qtbot, ui_scale=0.8)
    window.resize(1440, 900)
    window.show()
    window.apply_appearance()
    for _ in range(3):
        qtbot.wait(10)

    assert not window.console_dialog.isVisible(), "控制台默认不该自己弹出来"
    assert not hasattr(window, "tool_section"), "主窗口里不该再有常驻工具区"

    # 五个运行按钮**不开弹窗就能看见**
    run_buttons = (window.start_button, window.cancel_button, window.continue_button,
                   window.verify_button, window.open_dir_button)
    for button in run_buttons:
        assert button.isVisible(), f"「{button.text()}」应当常驻在主窗口底栏"

    # 与「控制台」同一行（y 相同），且控制台在它们右边、贴着窗口右端
    def pos(widget) -> QPoint:
        return widget.mapTo(window, widget.rect().topLeft())

    row_y = pos(window.start_button).y()
    for widget in (*run_buttons, window.console_button, window.status_label):
        assert abs(pos(widget).y() - row_y) <= 4, f"{widget.objectName() or widget.text()} 不在同一行"
    assert pos(window.console_button).x() > pos(window.open_dir_button).x(), "控制台应当在动作按钮右侧"
    assert pos(window.console_button).x() > pos(window.status_label).x(), "控制台应当在状态文字右侧"
    right_gap = window.width() - (pos(window.console_button).x() + window.console_button.width())
    assert right_gap <= 24, f"控制台没有贴到右端（右边还空着 {right_gap}px）"

    # 中栏整高只分给"脚本正文 / 轮次时间线"，工具区不再从里面切走一条
    pane_height = window.center_pane.height()
    script_height = window.center_pane.script_view.height()
    assert script_height >= pane_height * 0.6, (
        f"脚本正文只拿到 {script_height}px / 中栏 {pane_height}px"
    )

    # 弹窗里只剩低频面板（没有空的「运行」页）
    window.open_console(window.history_page)
    for _ in range(3):
        qtbot.wait(10)
    assert window.console_dialog.isVisible(), "点了控制台按钮弹窗没出来"
    assert window.tool_tabs.currentWidget() is window.history_page, "弹窗没停在请求的那一页"
    tabs = [window.tool_tabs.tabText(index) for index in range(window.tool_tabs.count())]
    assert "运行" not in tabs, f"控制台里不该再留一个空的运行页：{tabs}"
    assert window.center_pane.script_view.height() == script_height, (
        "弹窗不该改变主窗口里脚本正文的高度"
    )
    window.console_close_button.click()
    for _ in range(3):
        qtbot.wait(10)
    assert not window.console_dialog.isVisible(), "关闭按钮没关掉弹窗"

    # 窗口缩到允许的最小尺寸时，底栏那一行仍要不重叠、不越界
    # （再加第六个按钮就会在这里红：底栏已经用掉 951/960px）
    window.resize(window.minimumSize())
    for _ in range(3):
        qtbot.wait(10)
    boxes = []
    for widget in (*run_buttons, window.status_label, window.console_button):
        origin = pos(widget)
        boxes.append((widget.text(), origin.x(), widget.width()))
    for index, (name, x, width) in enumerate(boxes):
        assert x + width <= window.width(), f"最小宽度下「{name}」越出窗口右边缘"
        for other_name, other_x, other_width in boxes[index + 1:]:
            assert x + width <= other_x or other_x + other_width <= x, (
                f"最小宽度下「{name}」与「{other_name}」重叠"
            )
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


# ── 控制台弹窗：小屏上不许出界（底部是「关闭」按钮那一行）────────────────


def test_console_dialog_size_never_exceeds_a_small_screen():
    """弹窗尺寸规则：舒适值 860x560，小屏上收敛，**且下限不许超过屏幕**。

    1366x768 的笔记本在 150% 缩放下只有 910x512 逻辑像素 —— 560 高的弹窗比屏幕还高，
    底部的「关闭」按钮就点不到了。
    """
    from tu_shell_agent.ui.main_window import console_dialog_size_for

    assert console_dialog_size_for((1920, 1080)) == (860, 560), "大屏上应当是舒适尺寸"
    assert console_dialog_size_for((1280, 720)) == (860, 560), "用户的 1080p/150% 场景"

    for available in ((910, 512), (640, 480), (533, 533), (1024, 600)):
        width, height = console_dialog_size_for(available)
        assert width <= available[0] and height <= available[1], (
            f"{available} 下弹窗 {width}x{height} 超出屏幕"
        )
        # 别缩得没必要：装得下舒适尺寸就用舒适尺寸，装不下才收敛
        assert width >= min(860, int(available[0] * 0.9)), f"{available} 下弹窗被压得太窄：{width}"


def test_open_console_resizes_itself_to_fit_the_screen(qtbot):
    """真的打开一次：弹窗必须落在屏幕可用区之内，且「关闭」按钮可见可点。"""
    from tu_shell_agent.ui.main_window import available_screen_size

    window = _window(qtbot, ui_scale=0.8)
    window.show()
    window.open_console()
    qtbot.wait(30)

    dialog = window.console_dialog
    available = available_screen_size()
    assert dialog.width() <= available[0] and dialog.height() <= available[1], (
        f"弹窗 {dialog.width()}x{dialog.height()} 超出屏幕 {available}"
    )
    assert window.console_close_button.isVisible()
    assert window.start_button.isVisible(), "运行页的按钮也要在弹窗里看得见"
    dialog.accept()
    window.close()
