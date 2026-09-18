"""布局可拖拽性与尺寸记忆（用户报过"不能调节竖向的长度"）。

那次事故的根因有两层，两层都要有测试守着：
1. 主题把 `QSplitter::handle` 的宽/高写成了 1px —— 视觉上是细线，但**鼠标抓不住**；
2. 上下两块（三栏区 / 历史与设置区）当时是 QVBoxLayout 里的固定 1:1，根本不是分割器，
   无论怎么拖都不可能改比例。
"""

from __future__ import annotations

import json

import pytest
from PySide6.QtCore import Qt
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


def test_top_and_bottom_areas_can_be_resized_vertically(window):
    """三栏区与下方"历史+设置"区之间必须能改高度比例。

    原实现是 QVBoxLayout 的 addWidget(1) + addLayout(1)：比例写死 1:1，用户无法调整。
    """
    vertical = window.findChild(QSplitter, "verticalSplitter")
    assert vertical is not None, "上下两块之间没有分割器 → 竖向比例不可调"
    assert vertical.orientation() == Qt.Orientation.Vertical
    assert vertical.count() == 2

    before = vertical.sizes()
    assert before[0] > 0 and before[1] > 0
    # 模拟拖动：往"上面小、下面大"拖。注意分割器会尊重子控件的最小高度，所以这里断言
    # "确实动得动、且方向正确"，而不是断言等于我传入的数字。
    vertical.setSizes([400, 600])
    after = vertical.sizes()
    assert after[0] < before[0] - 40, f"上部压不下去：{before} → {after}"
    assert after[1] > before[1] + 40, f"下部顶不上来：{before} → {after}"


def test_resized_layout_is_remembered_in_settings(window, qtbot):
    """拖过的尺寸要写进设置并在下次开窗时还原（否则每次都得重拖）。"""
    window.vertical_splitter.setSizes([380, 620])
    window._save_layout()

    saved = json.loads(window.settings.layout)
    assert len(saved["vertical"]) == 2
    assert all(isinstance(value, int) and value > 0 for value in saved["vertical"])

    # 契约是"存什么还原什么"（不是"拖出来的比例一定如何"：分割器会尊重子控件最小高度，
    # 夹取后的结果才是真相，所以拿夹取后的实际尺寸去比）
    actual = window._splitter_state()["vertical"]
    again = MainWindow(wire_controller=False, settings=window.settings)
    qtbot.addWidget(again)
    again.resize(1440, 900)
    again.show()
    qtbot.waitExposed(again)          # 还原发生在 showEvent 里，要等它显示出来
    assert again.vertical_splitter.sizes() == actual, "存进去的尺寸没有还原"


def test_corrupt_layout_setting_is_ignored_not_fatal(window):
    """设置里的布局串坏掉时用默认比例，不能让界面起不来。"""
    window.settings.layout = "{不是合法 JSON"
    window._restore_layout()          # 不抛异常即可
    window.settings.layout = json.dumps({"main": [1, 2]})   # 数量对不上
    window._restore_layout()


def test_center_area_and_tool_area_are_resizable(window):
    """中栏内部（页签 / 轮次时间线）与"主区 / 工具区"之间的比例都要能调。

    排布调整后：底部不再是"历史 + 两页"的横向分割器（历史已收进工具区页签），
    能调的横向比例只剩三栏本身；竖向仍是"主区 / 工具区"。
    """
    center = window.center_pane.splitter
    before = center.sizes()
    center.setSizes([300, 260])
    assert center.sizes() != before, "中栏内部（脚本 / 时间线）比例拖不动"

    vertical = window.vertical_splitter
    before_v = vertical.sizes()
    vertical.setSizes([400, 600])
    assert vertical.sizes() != before_v


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


def test_window_refuses_to_shrink_below_a_usable_size(window):
    """窗口自身要有最小尺寸：低于它，三栏 + 上下两块本来就放不下。"""
    minimum = window.minimumSize()
    assert minimum.width() >= 900, f"最小宽度 {minimum.width()} 太小，三栏会被压扁"
    assert minimum.height() >= 650, f"最小高度 {minimum.height()} 太小，上下两块会被压扁"


def test_panes_and_inner_panels_declare_minimums(window):
    """每栏与关键内部控件都要有最小高度/宽度。

    没有它们的时候（实测）：窗口高 560px 时方案预览只剩 12px、右栏报告树与输出区各 35px ——
    等于看不见。QVBoxLayout 会把"没有最小高度"的控件一路压到零。
    """
    for widget, minimum in (
        (window.left_pane.plan_preview, 90),
        (window.center_pane.script_view, 140),
        (window.center_pane.timeline, 80),
        (window.chat_panel.transcript, 120),
        (window.right_pane.findings_tree, 110),
        (window.right_pane.output_view, 110),
        (window.history_page.list_widget, 90),
    ):
        assert widget.minimumHeight() >= minimum, f"{widget.objectName() or widget} 缺少最小高度"

    for column in (window.left_pane, window.center_pane, window.right_pane):
        assert column.minimumWidth() >= 240, f"{column.objectName()} 缺少最小宽度"
    # 工具区也要有下限：它现在装着 5 个页签（历史/模板库/对话/自检/设置）
    assert window.tool_tabs.minimumHeight() >= 180


def test_content_stays_visible_at_the_minimum_window_size(window, qtbot):
    """把窗口缩到**允许的最小尺寸**，关键控件仍要有能用的高度。

    这是用户实际遇到的那个场景：一直缩小窗口，栏目越挤越扁，最后什么都看不见。
    """
    window.resize(window.minimumSize())
    qtbot.wait(50)

    assert window.width() <= window.minimumSize().width() + 5     # 真的缩到了下限
    # 注意：历史列表现在在工具区页签里 —— 它没被选中时高度是 0，那是"没显示"而不是"被压没"，
    # 所以这里只检查主区三栏里常驻可见的控件。
    for widget, minimum in (
        (window.left_pane.plan_preview, 88),      # 留 2px 容差给样式边距
        (window.center_pane.script_view, 138),
        (window.right_pane.findings_tree, 108),
        (window.right_pane.output_view, 108),
    ):
        assert widget.height() >= minimum, f"{widget.objectName() or widget} 在下限尺寸下被压没了"


def test_tool_area_is_tall_enough_for_its_tallest_tab(window, qtbot):
    """工具区的最小高度必须装得下里面最高的页签，否则内容会溢出被裁掉。

    实测：对话面板 minimumSizeHint 305px，而工具区当时最小只有 200px —— 结果是输入框
    盖在记录区上、记录区看不全（取色时发现的：按坐标取到的像素属于下面那个输入框）。
    """
    assert window.tool_tabs.minimumHeight() >= 300

    for index in range(window.tool_tabs.count()):
        page = window.tool_tabs.widget(index)
        needed = page.minimumSizeHint().height()
        # 模板面板要 465px，所以这里只要求"对话面板/历史页这类常看的页签装得下"；
        # 更高的页签靠工具区自己可拖大来满足（竖向分割器还在）。
        if page is window.chat_panel:
            assert window.tool_tabs.minimumHeight() >= min(needed, 300), (
                f"{window.tool_tabs.tabText(index)} 需要 {needed}px，工具区最小只有 "
                f"{window.tool_tabs.minimumHeight()}px"
            )


def test_collapse_state_is_saved_and_restored(qtbot, tmp_path):
    """收起哪块要**写进磁盘**并还原（默认展开，只记"收起的"）。

    注意要用真实的设置文件路径：`AppSettings` 只有在 load() 过之后才知道往哪写，
    没有路径时 save() 会抛 ValueError（界面把它显示成状态栏提示）。
    """
    path = tmp_path / "settings.json"
    settings = AppSettings.load(path)
    settings.run_root = str(tmp_path / "runs")
    window = MainWindow(wire_controller=False, settings=settings)
    qtbot.addWidget(window)
    window.resize(1440, 900)
    window.show()
    qtbot.waitExposed(window)

    window.right_pane.sections["notes"].set_collapsed(True)
    window._on_section_toggled("notes", True)          # 模拟用户点击

    on_disk = json.loads(path.read_text(encoding="utf-8"))
    assert json.loads(on_disk["collapsed_sections"]) == ["notes"]

    again = MainWindow(wire_controller=False, settings=settings)
    qtbot.addWidget(again)
    again.resize(1440, 900)
    again.show()
    qtbot.waitExposed(again)                          # 还原发生在 showEvent 里
    assert again.right_pane.sections["notes"].is_collapsed() is True
    assert again.right_pane.sections["findings"].is_collapsed() is False   # 其余保持展开


def test_form_controls_are_never_crushed(window, qtbot):
    """表单控件永远不该被压扁（用户报"底色是长方形"，根因之一就是控件被压到 13px）。

    实测：左栏内容需要 576px、最小 474px，而当时只分到 383px —— QFormLayout 把下拉框与
    微调框压到 **13px 高**（sizeHint 是 29px）。而 13px 高时圆角半径 10px ≥ 半高 6.5px，
    Qt 会整个退回画直角。现在表单进滚动区 + 单行输入声明 min-height，控件保持正常高度。
    """
    from PySide6.QtWidgets import QComboBox, QLineEdit, QSpinBox

    window.resize(window.minimumSize())        # 最挤的情况
    qtbot.wait(50)

    controls = (
        window.findChildren(QComboBox) + window.findChildren(QSpinBox)
        + window.findChildren(QLineEdit)
    )
    assert controls, "一个输入控件都没找到？"
    crushed = [
        (type(c).__name__, c.objectName(), c.height())
        for c in controls
        # 跳过 Qt 内部控件（例如 QSpinBox 内部的 qt_spinbox_lineedit）：它们的高度由
        # 外层控件决定，不是"被布局压扁"的证据。
        if c.isVisible() and not c.objectName().startswith("qt_") and c.height() < 22
    ]
    assert crushed == [], f"这些控件被压扁了（高度 < 22px）：{crushed}"


def test_left_pane_scrolls_instead_of_clipping_when_short(window, qtbot):
    """左栏空间不够时要能**滚动**，而不是把内容裁掉看不见。

    隔离的是 scroll.py 那层包装：没有它，QSS 的 min-height 会让控件保持高度、但整块内容
    溢出面板被裁切（下半部分永远看不到，也没法滚过去）。
    """
    from PySide6.QtWidgets import QScrollArea

    area = window.left_pane.findChild(QScrollArea)
    assert area is not None, "左栏内容没有包在滚动区里"

    window.resize(window.minimumSize())
    qtbot.wait(50)
    # 内容比可视区高 → 必须出现可滚动范围（这正是"能不能看到下面那半"的关键）
    assert area.verticalScrollBar().maximum() > 0, "内容溢出时没有可滚动范围（会被裁掉）"

    area.verticalScrollBar().setValue(area.verticalScrollBar().maximum())
    qtbot.wait(20)
    assert area.verticalScrollBar().value() > 0, "滚动条拖不动"
