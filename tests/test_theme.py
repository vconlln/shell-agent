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
    assert css("bg") == "#181818"                     # 不透明的原样给 hex
    color = qcolor("fg_secondary")
    assert (color.red(), color.green(), color.blue(), color.alpha()) == (255, 255, 255, 179)


def test_theme_tokens_match_codex_desktop_values():
    """关键令牌必须与 Codex 桌面版 electron-dark 作用域一致（改动要是有意的）。"""
    assert TOKENS["bg"] == "#181818"          # --gray-900
    assert TOKENS["bg_elevated"] == "#212121"  # --gray-800
    assert TOKENS["fg"] == "#ffffff"           # --gray-0
    assert TOKENS["accent"] == "#339cff"       # --blue-300
    assert TOKENS["error"] == "#ff6764"        # --red-300
    assert TOKENS["ok"] == "#40c977"           # --green-300
    assert TOKENS["row_height"] == "30px"


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

    assert rendered(window) == "#181818"                              # 主背景 --gray-900
    assert rendered(window.center_pane.script_view) == "#0d0d0d"      # 只读底 --gray-1000
    assert rendered(window.right_pane.output_view) == "#0d0d0d"
    # 主操作（开始）是白底黑字，与 Codex 一致；取中心点避开圆角
    center = QPoint(window.start_button.width() // 2, window.start_button.height() // 2)
    assert rendered(window.start_button, center) == "#ffffff"


def test_pane_headers_exist_for_all_three_columns(themed_app, qtbot):
    """三栏各有一行分区小标题（Codex 的分区感来自栏头，不是靠边框）。"""
    from PySide6.QtWidgets import QLabel

    from tu_shell_agent.ui.main_window import MainWindow

    window = MainWindow()
    qtbot.addWidget(window)
    # 按 set 比：findChildren 的遍历顺序是实现细节，不是契约
    headers = {label.text() for label in window.findChildren(QLabel, "paneHeader")}
    assert headers == {"方案与模板", "脚本与轮次", "校验与输出"}
