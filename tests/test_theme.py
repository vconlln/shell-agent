"""主题的测试契约：Codex 深色令牌真的落到了渲染上，而不是只写进字符串。

这里的断言分两层：
1. **字符串层**：样式表里不许出现 8 位 hex —— Qt 把 8 位 hex 当 `#AARRGGBB` 解析
   （不是 CSS 的 `#RRGGBBAA`），Codex 的 `#ffffff0a`（4% 白）在 Qt 里会变成不透明的黄，
   实测渲染成 #ffff0a。这个错误 QSS 不报错、palette 也不报错，只有截图取色才看得见。
2. **像素层**：真的把窗口画出来取色。字符串对了不等于画对了（样式没命中、被原生样式盖住、
   被 :disabled/:read-only 分支顶掉，都只有取色能发现）。
"""

from __future__ import annotations

import re

import pytest
from PySide6.QtCore import QPoint
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QApplication

from tu_shell_agent.ui.theme import TOKENS, apply_theme, build_stylesheet, css, qcolor


def test_stylesheet_has_no_eight_digit_hex():
    """8 位 hex 是 Qt 的坑：它按 #AARRGGBB 解析，写 #RRGGBBAA 会得到完全不透明的怪色。"""
    sheet = build_stylesheet()
    offenders = [line for line in sheet.splitlines() if re.search(r"#[0-9a-fA-F]{8}\b", line)]
    assert offenders == [], f"样式表里出现 8 位 hex：{offenders}"


def test_css_and_qcolor_render_translucent_tokens_correctly():
    """半透明令牌必须经出口函数转成 rgba()/QColor(r,g,b,a)，不能当 hex 用。"""
    assert css("bg_hover") == "rgba(255, 255, 255, 10)"
    assert css("bg") == "#101114"                     # 不透明的原样给 hex
    color = qcolor("fg_secondary")
    assert (color.red(), color.green(), color.blue(), color.alpha()) == (236, 237, 240, 178)


def test_theme_tokens_are_the_documented_palette():
    """关键令牌固定下来（改动必须是有意的，并且同步改这条测试与设计文档）。

    注意：这套色板**已经不是 Codex 原值**了。2026-09-19 用户要求"显得高级、尽量圆角"，
    于是从 Codex 的 #181818 往冷调更深走、文字不用纯白、强调色换靛蓝、语义色降饱和、
    圆角全面放大。照抄的是它的**结构与比例**（分层关系、字号、行高、三级文字），
    不是具体色值 —— 这条测试锁的就是"现在的有意取值"，不是"与 Codex 逐字一致"。
    """
    assert TOKENS["bg"] == "#101114"
    assert TOKENS["bg_elevated"] == "#17181c"
    assert TOKENS["bg_under"] == "#0b0c0e"
    assert TOKENS["fg"] == "#ecedf0"
    assert TOKENS["accent"] == "#7c9cff"
    assert TOKENS["error"] == "#f2707a"
    assert TOKENS["ok"] == "#4ec98a"
    assert TOKENS["row_height"] == "30px"
    # 圆角："尽量圆角"是用户明确要求，锁住它别再被改回 6px
    assert TOKENS["radius"] == "10px"
    assert TOKENS["radius_lg"] == "14px"
    # 页签半径必须 < 页签半高（Qt 否则退回直角），所以它不是 999px 那种"胶囊写法"
    assert TOKENS["radius_pill"] == "11px"
    assert int(TOKENS["radius_pill"].rstrip("px")) < 24 // 2 + 1
    # 分割条的**可抓宽度**（里程碑：曾经是 1px，用户抓不住）
    assert int(TOKENS["handle"].rstrip("px")) >= 6


@pytest.fixture
def themed_app(qtbot):
    """装上主题并把全局状态复原，别把样式泄漏给同一会话里的其他测试。"""
    app = QApplication.instance()
    previous_sheet = app.styleSheet()
    previous_palette = app.palette()
    previous_style = app.style().objectName()
    try:
        yield app
    finally:
        app.setStyleSheet(previous_sheet)
        app.setPalette(previous_palette)
        if previous_style:
            app.setStyle(previous_style)


def test_window_actually_renders_with_codex_dark_colors(themed_app, qtbot):
    """像素层验证：主背景、只读区、主按钮的实际渲染色。

    只断言"样式表非空"是不够的：样式没命中、或被 Fusion 的原生绘制盖住，
    字符串照样是对的，界面还是花的。
    """
    from tu_shell_agent.ui.main_window import MainWindow

    apply_theme(themed_app)
    window = MainWindow()
    qtbot.addWidget(window)
    window.resize(1440, 900)
    window.show()
    qtbot.waitExposed(window)

    image = window.grab().toImage()

    def rendered(widget, offset: QPoint = QPoint(6, 6)) -> str:
        point = widget.mapTo(window, widget.rect().topLeft() + offset)
        return QColor(image.pixel(point.x(), point.y())).name()

    assert rendered(window) == "#101114"                              # 主背景
    assert rendered(window.center_pane.script_view) == "#0b0c0e"      # 只读底（更深一档）
    assert rendered(window.right_pane.output_view) == "#0b0c0e"
    # 主操作（开始）是浅底深字；取中心点避开圆角
    center = QPoint(window.start_button.width() // 2, window.start_button.height() // 2)
    assert rendered(window.start_button, center) == "#ecedf0"


def test_pane_headers_exist_for_all_three_columns(themed_app, qtbot):
    """三栏各有一行分区小标题（Codex 的分区感来自栏头，不是靠边框）。"""
    from PySide6.QtWidgets import QLabel

    from tu_shell_agent.ui.main_window import MainWindow

    window = MainWindow()
    qtbot.addWidget(window)
    # 按 set 比：findChildren 的遍历顺序是实现细节，不是契约
    # 排布调整后左栏改叫「方案与运行参数」（模板库移进了工具区），并多了「工具区」栏头
    headers = {label.text() for label in window.findChildren(QLabel, "paneHeader")}
    assert headers == {"方案与运行参数", "脚本与轮次", "校验与输出", "工具区"}


def test_chat_panel_and_extra_box_are_themed(themed_app, qtbot):
    """新增的两个控件也要在深色主题里（否则会出现浅色残留的输入框）。"""
    from tu_shell_agent.ui.main_window import MainWindow

    apply_theme(themed_app)
    window = MainWindow()
    qtbot.addWidget(window)
    window.resize(1440, 900)
    window.show()
    qtbot.waitExposed(window)

    image = window.grab().toImage()

    def rendered(widget, offset: QPoint = QPoint(8, 8)) -> str:
        point = widget.mapTo(window, widget.rect().topLeft() + offset)
        return QColor(image.pixel(point.x(), point.y())).name()

    # 对话面板在工具区页签里（未选中时不渲染），所以用**控件自己的帧**取色：
    # 从整窗帧里按坐标取会被页签裁切/相邻控件遮挡影响（这条用例第一版就是这么失败的）。
    window.tool_tabs.setCurrentWidget(window.chat_panel)
    qtbot.wait(50)

    def own_color(widget) -> str:
        frame = widget.grab().toImage()
        return QColor(frame.pixel(widget.width() // 2, widget.height() // 2)).name()

    assert own_color(window.chat_panel.transcript) == "#0b0c0e"      # 只读记录区
    # 输入控件的底色与圆角由 test_form_controls_are_rounded... 用**隔离窗口**验证：
    # 左栏现在可滚动，被滚出视野的控件单独 grab() 会得到空白帧（实测全黑），断言不可靠。


# ── 圆角真的画出来了（不只是写了 border-radius）────────────────────────────


def test_rounded_corners_are_actually_rendered(themed_app, qtbot):
    """取角落与中心两个像素：圆角生效时，角落露出的应该是父底而不是控件自己的底色。

    只断言"QSS 里有 border-radius"是不够的：控件没吃到那条规则、或被 :read-only 之类
    的分支顶掉，字符串照样对，界面还是直角。
    """
    from PySide6.QtWidgets import QListWidget

    from tu_shell_agent.ui.main_window import MainWindow

    apply_theme(themed_app)
    window = MainWindow()
    qtbot.addWidget(window)
    window.resize(1440, 900)
    window.show()
    qtbot.waitExposed(window)

    history = window.history_page.list_widget      # bg_elevated + 14px 圆角（QSS 里定的）
    assert isinstance(history, QListWidget)
    image = window.grab().toImage()

    def sample(offset: QPoint) -> str:
        point = history.mapTo(window, history.rect().topLeft() + offset)
        return QColor(image.pixel(point.x(), point.y())).name()

    center = sample(QPoint(history.width() // 2, history.height() // 2))
    corner = sample(QPoint(1, 1))

    assert center == "#17181c", "列表底色没吃到主题"
    assert corner != center, "角落与中心同色 → 圆角没生效（被画成直角了）"
    # 角落应当是"父底色"（可能叠了一丝边框），明显比控件底色更接近页面背景
    def distance(a: str, b: str) -> int:
        pa, pb = QColor(a), QColor(b)
        return sum(abs(x - y) for x, y in zip(pa.getRgb()[:3], pb.getRgb()[:3]))

    assert distance(corner, "#101114") < distance(corner, "#17181c")


def test_tab_corners_are_actually_rounded(themed_app, qtbot):
    """页签的圆角必须真的画出来 —— 这条是用户报出来的（"这几个按钮不是圆角的"）。

    踩的坑：Qt 画页签时，**圆角半径 >= 页签高度的一半就整个退回直角**。
    原来写的 `border-radius: 999px`（CSS 里常见的"胶囊"写法）在这里不是"更大的圆角"，
    而是"完全没有圆角"；而 QSS 字符串层面看不出任何问题 —— 只有取色能发现。
    """
    from PySide6.QtWidgets import QTabWidget

    from tu_shell_agent.ui.main_window import MainWindow

    apply_theme(themed_app)
    window = MainWindow()
    qtbot.addWidget(window)
    window.resize(1440, 900)
    window.show()
    qtbot.waitExposed(window)

    for tabs in window.findChildren(QTabWidget):
        bar = tabs.tabBar()
        if bar.count() == 0:
            continue
        index = bar.currentIndex()
        rect = bar.tabRect(index)                     # 取选中的那个：它有填充色，才能比较
        image = bar.grab().toImage()

        def color(dx: int, dy: int) -> str:
            return QColor(image.pixel(rect.x() + dx, rect.y() + dy)).name()

        corner = color(1, 1)
        fill = color(rect.width() // 2, rect.height() - 4)
        assert corner != fill, (
            f"{tabs.objectName() or tabs} 的选中页签是直角（半径 {TOKENS['radius_pill']} 可能 >= 页签半高）"
        )


def test_form_controls_are_rounded_when_they_have_a_normal_height(themed_app, qtbot):
    """输入类控件的圆角：在隔离窗口里验证（避免滚动区裁切/坐标偏差骗过取色）。

    为什么用隔离窗口：在真实主窗口里，左栏内容比可见区域高，控件可能被滚动区裁掉一部分，
    `mapTo()` 给出的坐标会落到别的控件上 —— 取色结果就不是这个控件的（这次排查被坑过）。
    """
    from PySide6.QtWidgets import (
        QComboBox, QLineEdit, QPlainTextEdit, QPushButton, QSpinBox, QVBoxLayout, QWidget,
    )

    apply_theme(themed_app)
    host = QWidget()
    host.setObjectName("paneCard")           # 和真实场景一样，父容器是"卡片"
    qtbot.addWidget(host)
    host.resize(320, 280)
    layout = QVBoxLayout(host)
    controls = {
        "QComboBox": QComboBox(),
        "QSpinBox": QSpinBox(),
        "QLineEdit": QLineEdit(),
        "QPlainTextEdit": QPlainTextEdit(),
        "QPushButton": QPushButton("开始"),
    }
    for widget in controls.values():
        widget.setMinimumHeight(30)
        layout.addWidget(widget)
    host.show()
    qtbot.waitExposed(host)

    image = host.grab().toImage()
    for name, widget in controls.items():
        def color(x: int, y: int) -> str:
            point = widget.mapTo(host, QPoint(x, y))
            return QColor(image.pixel(point.x(), point.y())).name()

        corner = color(1, 1)
        center = color(widget.width() // 2, widget.height() // 2)
        assert corner != center, f"{name} 是矩形底（圆角没生效，或控件被压得比半径还矮）"


def test_input_surface_differs_from_the_panel_so_rounding_is_visible():
    """输入框底色必须与卡片底色不同。

    同色时圆角处的像素和填充一个颜色 —— 形状在视觉上根本不存在（用户报的
    "圆角边框 + 长方形底色"有一部分就是这么来的）。
    """
    assert TOKENS["bg_input"] != TOKENS["bg_elevated"]
    assert TOKENS["bg_input"] != TOKENS["bg"]


def test_input_surface_differs_from_the_panel_so_rounding_is_visible():
    """输入框底色必须与卡片底色不同。

    同色时圆角处的像素和填充一个颜色 —— 形状在视觉上根本不存在（用户报的
    "圆角边框 + 长方形底色"有一部分就是这么来的）。
    """
    assert TOKENS["bg_input"] != TOKENS["bg_elevated"]
    assert TOKENS["bg_input"] != TOKENS["bg"]


def test_single_line_controls_refuse_to_shrink_below_a_usable_height(themed_app, qtbot):
    """单行输入控件在**被挤压的布局**里也要保持可用高度。

    这条隔离的是 QSS 的 `min-height`：把它撤掉，控件在 cramped 布局里会缩到 13px，
    而 13px 时圆角半径 ≥ 半高 → Qt 画直角（"圆角边框 + 长方形底色"的成因之一）。
    """
    from PySide6.QtWidgets import QComboBox, QSpinBox, QVBoxLayout, QWidget

    apply_theme(themed_app)
    host = QWidget()
    qtbot.addWidget(host)
    host.resize(240, 20)                  # 故意给一个装不下的高度
    layout = QVBoxLayout(host)
    layout.setContentsMargins(0, 0, 0, 0)
    combo, spin = QComboBox(), QSpinBox()
    layout.addWidget(combo)
    layout.addWidget(spin)
    host.show()
    qtbot.waitExposed(host)

    assert combo.height() >= 24, f"下拉框在挤压下缩到了 {combo.height()}px"
    assert spin.height() >= 24, f"微调框在挤压下缩到了 {spin.height()}px"
