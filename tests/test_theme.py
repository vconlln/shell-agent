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
    assert TOKENS["radius_pill"] == "999px"
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
    headers = {label.text() for label in window.findChildren(QLabel, "paneHeader")}
    assert headers == {"方案与模板", "脚本与轮次", "校验与输出"}


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

    chat = window.center_pane.chat
    assert rendered(chat.transcript) == "#0b0c0e"        # 只读记录区
    assert rendered(window.left_pane.extra_edit) == "#17181c"   # 可编辑输入框


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
