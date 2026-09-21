"""布局可拖拽性与尺寸记忆（用户报过"不能调节竖向的长度"）。

那次事故的根因有两层，两层都要有测试守着：
1. 主题把 `QSplitter::handle` 的宽/高写成了 1px —— 视觉上是细线，但**鼠标抓不住**；
2. 上下两块（三栏区 / 历史与设置区）当时是 QVBoxLayout 里的固定 1:1，根本不是分割器，
   无论怎么拖都不可能改比例。
"""

from __future__ import annotations

import json

import pytest
from PySide6.QtCore import QSize, Qt
from PySide6.QtWidgets import QSplitter

from tu_shell_agent.ui.main_window import MainWindow
from tu_shell_agent.ui.settings import AppSettings


@pytest.fixture
def window(qtbot, tmp_path):
    """带主题的主窗口。

    必须装上主题：`min-height` 这类约束是通过 QSS 生效的，不装主题测的是"没有样式表的世界"，
    而用户看的是带样式表的版本（这个坑已经踩过一次：控件高度 21px 的判定只有在装了主题后
    才反映真实情况）。用完把全局样式表还原，别泄漏给同一会话的其他测试。
    """
    from PySide6.QtWidgets import QApplication

    from tu_shell_agent.ui.theme import apply_theme

    app = QApplication.instance()
    previous_sheet = app.styleSheet()
    previous_palette = app.palette()
    previous_style = app.style().objectName()
    apply_theme(app)
    try:
        win = MainWindow(
            wire_controller=False,
            settings=AppSettings(run_root=str(tmp_path / "runs"), templates_dir=str(tmp_path / "tpl")),
        )
        qtbot.addWidget(win)
        win.resize(1440, 900)
        win.show()
        qtbot.waitExposed(win)
        yield win
    finally:
        app.setStyleSheet(previous_sheet)
        app.setPalette(previous_palette)
        if previous_style:
            app.setStyle(previous_style)


def test_all_splitters_have_a_grabbable_handle(window):
    """分割条的可抓宽度必须够（1px 的细线抓不住，这是用户实际遇到的问题）。"""
    splitters = window.findChildren(QSplitter)
    assert splitters, "一个分割器都没有？"
    for splitter in splitters:
        assert splitter.handleWidth() >= 6, f"{splitter.objectName()} 的把手只有 {splitter.handleWidth()}px，抓不住"


def test_center_areas_can_be_resized_vertically(window):
    """竖向比例要能调。

    以前调的是"三栏区 / 底部工具区"那条分割器；工具区搬进「控制台」弹窗之后，主窗口里
    唯一还需要竖向调节的就是中栏的"脚本正文 / 轮次时间线"。
    """
    vertical = window.center_pane.splitter
    assert vertical.orientation() == Qt.Orientation.Vertical
    assert vertical.count() == 2

    before = vertical.sizes()
    assert before[0] > 0 and before[1] > 0
    # 模拟拖动：往"上面小、下面大"拖。分割器会尊重子控件最小高度，所以断言"动得动、
    # 方向也对"，而不是断言等于传入的数字。
    vertical.setSizes([200, 700])
    after = vertical.sizes()
    assert after[0] < before[0], f"上部压不下去：{before} → {after}"
    assert after[1] > before[1], f"下部顶不上来：{before} → {after}"


def test_resized_layout_is_remembered_in_settings(window, qtbot):
    """拖过的尺寸要写进设置并在下次开窗时还原（否则每次都得重拖）。"""
    window.center_pane.splitter.setSizes([380, 620])
    window._save_layout()

    saved = json.loads(window.settings.layout)
    assert len(saved["center"]) == 2, "中栏的脚本/时间线比例没被记下来"
    assert all(isinstance(value, int) and value > 0 for value in saved["center"])

    # 契约是"存什么还原什么"（不是"拖出来的比例一定如何"：分割器会尊重子控件最小高度，
    # 夹取后的结果才是真相，所以拿夹取后的实际尺寸去比）
    actual = window._splitter_state()["center"]
    again = MainWindow(wire_controller=False, settings=window.settings)
    qtbot.addWidget(again)
    again.resize(1440, 900)
    again.show()
    qtbot.waitExposed(again)          # 还原发生在 showEvent 里，要等它显示出来
    assert again.center_pane.splitter.sizes() == actual, "存进去的尺寸没有还原"


def test_corrupt_layout_setting_is_ignored_not_fatal(window):
    """设置里的布局串坏掉时用默认比例，不能让界面起不来。"""
    window.settings.layout = "{不是合法 JSON"
    window._restore_layout()          # 不抛异常即可
    window.settings.layout = json.dumps({"main": [1, 2]})   # 数量对不上
    window._restore_layout()


def test_center_area_and_columns_are_resizable(window):
    """能拖的比例都要真的能拖：三栏横向，以及中栏内部（脚本 / 轮次时间线）竖向。

    排布调整后主窗口里只剩横向三栏 + 中栏自己那条竖向分割条（工具区搬进了控制台弹窗，
    不再从主窗口高度里切一块）。
    """
    main = window.splitter
    before = main.sizes()
    main.setSizes([260, 700, 480])
    assert main.sizes() != before, "三栏横向比例拖不动"

    center = window.center_pane.splitter
    before = center.sizes()
    center.setSizes([300, 260])
    assert center.sizes() != before, "中栏内部（脚本 / 时间线）比例拖不动"


def test_handle_width_does_not_depend_on_the_stylesheet(qtbot, tmp_path):
    """把手宽度必须在代码里设死，不能只靠 QSS。

    实测：QSS 的 `QSplitter::handle { width }` 确实会影响它，但**样式表没加载时**会退回
    Qt 默认的 4px，而 4px 抓不住。把一个"能不能用鼠标操作"的属性交给样式表是脆的。
    """
    plain = MainWindow(
        wire_controller=False,
        settings=AppSettings(run_root=str(tmp_path / "r"), templates_dir=str(tmp_path / "t")),
    )
    qtbot.addWidget(plain)          # 故意不装主题（不调 apply_theme）
    for splitter in plain.findChildren(QSplitter):
        assert splitter.handleWidth() >= 6, splitter.objectName()


# ── 缩小窗口时内容不许被压没（用户报过"挤压到看不见"）─────────────────────


def test_window_minimum_is_usable_and_fits_the_screen(window):
    """窗口最小尺寸既要"够用"，也**不能超过屏幕**。

    以前写死 960x780，在 Windows 150% 缩放（1080p 只有 720 逻辑像素高）下比屏幕还高 ——
    窗口管理器照给，布局只能违反最小尺寸，于是出现重叠与出框（用户反馈的三条症状）。
    现在的规则：按屏幕可用区的九成/八成半收敛，并保留 720x520 的可用下限。
    """
    from tu_shell_agent.ui.main_window import available_screen_size, window_minimum_for

    expected = window_minimum_for(available_screen_size())
    minimum = window.minimumSize()
    assert (minimum.width(), minimum.height()) == expected
    assert minimum.width() >= 720 and minimum.height() >= 520, "最小尺寸被压到不可用"
    available = available_screen_size()
    assert minimum.width() <= available[0] and minimum.height() <= available[1], (
        "最小尺寸超过了屏幕可用区 —— 布局会被迫违反最小尺寸"
    )


def test_panes_and_inner_panels_declare_minimums(window):
    """每栏与关键内部控件都要有最小高度/宽度。

    没有它们的时候（实测）：窗口高 560px 时方案预览只剩 12px、右栏报告树与输出区各 35px ——
    等于看不见。QVBoxLayout 会把"没有最小高度"的控件一路压到零。
    """
    # 数值在 2026-09-20 下调过一次：Windows 150% 缩放下 1080p 只有 720 逻辑像素高，
    # 原来那套（脚本视图 140 / 对话记录 120 / 右栏 110+110+80）把整窗地板抬到 666，
    # 屏幕装不下就开始违反最小尺寸 —— 于是右栏被压缩、对话面板与输入框重叠。
    # 下调后仍保留"看得见几行"的下限，整窗地板降到 666 以内。
    for widget, minimum in (
        (window.left_pane.plan_preview, 90),
        (window.center_pane.script_view, 80),
        (window.center_pane.timeline, 80),
        (window.chat_panel.transcript, 64),
        (window.right_pane.findings_tree, 52),
        (window.right_pane.output_view, 52),
        (window.history_page.list_widget, 90),
    ):
        assert widget.minimumHeight() >= minimum, f"{widget.objectName() or widget} 缺少最小高度"

    for column in (window.left_pane, window.center_pane, window.right_pane):
        assert column.minimumWidth() >= 240, f"{column.objectName()} 缺少最小宽度"
    # 工具区（在控制台弹窗里）也要有下限：它装着 5 个页签（运行/历史/模板库/自检/设置）
    assert window.tool_tabs.minimumHeight() >= 180
    # 右列的页签装着对话面板，页签本体的下限不该低于里面最高的一页
    assert window.right_tabs.minimumSizeHint().height() >= window.chat_panel.minimumSizeHint().height()


def test_content_stays_visible_at_the_minimum_window_size(window, qtbot):
    """把窗口缩到**允许的最小尺寸**，关键控件仍要有能用的高度。

    这是用户实际遇到的那个场景：一直缩小窗口，栏目越挤越扁，最后什么都看不见。
    """
    window.resize(window.minimumSize())
    qtbot.wait(50)

    assert window.width() <= window.minimumSize().width() + 5     # 真的缩到了下限
    # 「校验与输出」在右栏第二个页签里，不选中它高度就是 0 —— 那是"没显示"不是"被压没"，
    # 所以先把它切到前台再量。
    window.right_tabs.setCurrentWidget(window.right_pane)
    qtbot.wait(50)
    # 对话面板（右栏第一页）在下限尺寸下也要装得下
    assert window.right_tabs.height() >= window.chat_panel.minimumSizeHint().height(), (
        f"右栏只有 {window.right_tabs.height()}px，装不下对话面板"
    )
    for widget, minimum in (
        (window.left_pane.plan_preview, 88),      # 留 2px 容差给样式边距
        (window.center_pane.script_view, 78),
        (window.right_pane.findings_tree, 50),
        (window.right_pane.output_view, 50),
    ):
        assert widget.height() >= minimum, f"{widget.objectName() or widget} 在下限尺寸下被压没了"


def test_console_dialog_fits_its_tallest_page(window, qtbot):
    """控制台弹窗的页签区必须装得下里面最高的一页，否则内容会溢出被裁掉。

    实测（搬进弹窗之前，历史事故）：对话面板 minimumSizeHint 305px，而工具区当时最小只有
    200px —— 结果是输入框盖在记录区上、记录区看不全。现在这些页在弹窗里，弹窗得自己够高；
    页签区高度按"最高一页的 minimumSizeHint"算，不是按弹窗总高拍脑袋。
    """
    window.open_console()
    qtbot.wait(50)

    tabs = window.tool_tabs
    content_height = tabs.height() - tabs.tabBar().height()
    for index in range(tabs.count()):
        page = tabs.widget(index)
        needed = page.minimumSizeHint().height()
        assert content_height >= needed, (
            f"「{tabs.tabText(index)}」需要 {needed}px，页签区只有 {content_height}px（会溢出被裁）"
        )


def test_right_pane_collapses_automatically_by_height(window, qtbot):
    """右栏三块按**可用高度自动**收起（用户裁定：这里不需要手动折叠）。

    优先级：校验报告 > 执行输出 > 模型取舍说明与假设。

    新布局（2026-09）下右栏是右列的「校验与输出」页签，高度由**窗口高度**决定，不再由竖向
    分割条决定（主线里已经没有竖向分割器了）。所以这里用窗口高度触发，并且先临时放开最小
    尺寸来模拟 Windows 150% 缩放下的小屏可用高度 —— 在 1080p 上主窗口本来就只能拿到
    700 逻辑像素左右，右栏确实会被压到需要收起的高度。
    """
    sections = window.right_pane.sections

    def collapsed() -> set[str]:
        return {key for key, section in sections.items() if section.is_collapsed()}

    # 隐藏的页签不参与布局（取到的是过期几何），要先切到这个页签上
    window.right_tabs.setCurrentWidget(window.right_pane)
    qtbot.wait(50)
    assert collapsed() == set(), "空间足够时不该折叠"

    window.setMinimumSize(QSize(560, 360))      # 模拟小屏：允许窗口比"推荐最小值"更矮
    window.resize(1200, 400)
    qtbot.wait(50)
    assert "notes" in collapsed(), "空间不足时应先收起「模型取舍说明与假设」"

    window.resize(1200, 320)
    qtbot.wait(50)
    assert "output" in collapsed(), "更挤时应连「执行输出」一起收起"
    assert "findings" not in collapsed(), "「校验报告」是主要结论，不该被自动收起"


def test_right_pane_expands_again_when_space_returns(window, qtbot):
    """把空间还回来要自动展开 —— 这才是"不需要手动"的关键。"""
    sections = window.right_pane.sections
    window.right_tabs.setCurrentWidget(window.right_pane)
    window.setMinimumSize(QSize(560, 360))
    window.resize(1200, 320)
    qtbot.wait(50)
    assert any(section.is_collapsed() for section in sections.values())

    window.resize(1400, 900)                    # 空间回来了
    qtbot.wait(50)
    collapsed = {key for key, section in sections.items() if section.is_collapsed()}
    assert collapsed == set(), f"空间回来了还收着：{sorted(collapsed)}"


def test_section_header_is_plain_text(window, qtbot):
    """区块标题是纯文字：没有折叠箭头，也不是按钮（用户裁定：自动折叠不该留手动暗示）。

    收起状态靠"标题变淡"提示（动态属性 collapsed）—— 再挂一个 ▾/▸ 会让人以为要点它。
    """
    for section in window.right_pane.sections.values():
        assert not section.header.text().startswith(("▾", "▸", "▶", "▼"))
        assert section.header.text() in {
            "校验报告", "执行输出", "模型取舍说明与假设"
        }
        # QLabel 没有 clicked 信号 —— 同时锁住"不要再把它做回按钮"
        assert not hasattr(section.header, "clicked")
        assert section.header.property("collapsed") in ("true", "false")

    # 收起时属性要跟着变（样式表靠它把标题调淡）
    section = window.right_pane.sections["notes"]
    section.set_collapsed(True)
    assert section.header.property("collapsed") == "true"
    section.set_collapsed(False)
    assert section.header.property("collapsed") == "false"


def test_left_pane_scrolls_instead_of_clipping_when_short(qtbot):
    """左栏空间不够时要能**滚动**，而不是把内容裁掉看不见。

    在**控件层面**隔离这件事（这正是 `scroll.py` 那层的职责）：把左栏直接缩到远小于内容高度。
    不要再借窗口最小尺寸制造溢出 —— 2026-09-20 为高 DPI 下调各处最小高度之后，
    窗口最小尺寸下左栏内容已经放得下（滚动范围 0），那条前提就没了。
    """
    from PySide6.QtWidgets import QScrollArea

    from tu_shell_agent.ui.panes.left import LeftPane

    pane = LeftPane()
    qtbot.addWidget(pane)
    pane.resize(260, 180)          # 远小于内容高度
    pane.show()
    qtbot.wait(20)

    area = pane.findChild(QScrollArea)
    assert area is not None, "左栏内容没有包在滚动区里"
    assert area.verticalScrollBar().maximum() > 0, "内容溢出时没有可滚动范围（会被裁掉）"

    area.verticalScrollBar().setValue(area.verticalScrollBar().maximum())
    qtbot.wait(20)
    assert area.verticalScrollBar().value() > 0, "滚动条拖不动"


def test_default_columns_leave_room_for_the_left_pane(window, qtbot):
    """默认三栏比例：右列（模型对话）变宽之后，左栏仍然拿得到 300px 以上。

    实测（修前）：对话面板里两个下拉写死了 240 / 200 的最小宽度，把右列的**最小宽度**顶到
    599px —— 1440 宽的窗口里右列吃 599、左栏只剩 284（意图是 320）。这就是用户抱怨过的
    "挤压到看不见"那一类：某一块的最小尺寸把别人的空间吃掉了。
    """
    window.resize(1440, 900)
    qtbot.wait(50)

    left, center, right = window.splitter.sizes()
    assert left >= 300, f"左栏只剩 {left}px"
    assert center >= 500, f"中栏只剩 {center}px"
    assert right >= 480, f"右列只剩 {right}px"

    minimum = window.right_tabs.minimumSizeHint().width()
    assert minimum <= 420, f"右列的最小宽度是 {minimum}px，会把左栏挤扁"
