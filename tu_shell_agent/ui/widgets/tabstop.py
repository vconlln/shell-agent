"""把文本控件的制表位设成"若干个空格宽"。

**为什么要单独一个模块**：Qt 的默认制表位是 **8 个字符宽**（≈80px），而 shell 与 Markdown
里用 tab 缩进是常态 —— 默认值会让缩进宽出一倍（用户："Tab 的长度有点长，应该是四个空格的长度"）。

**为什么不能只在构造时设一次**：`fontMetrics()` 量的是**当前字体**，而本项目的字体是 QSS 给的
（`QPlainTextEdit#scriptView { font-family: mono; font-size: ... }`），QSS 在控件 polish 之后才生效 ——
构造时量到的是构造时那个字体，实测得到的制表位比"4 个空格"小 4px（24 vs 28）。
所以统一走 `apply_shell_tab_stop()`：构造时调一次、字体变化时再调一次。
"""

from __future__ import annotations

# 一档制表位 = 4 个空格（Google Shell Style Guide 与 Python 社区的通行值）
TAB_SPACES = 4


def _measure_font(widget):
    """量空格宽度用的字体：**文档默认字体**（那才是文字真正按它排版的那个）。

    只看 `widget.font()` 会漏掉 QSS：面板字体是样式表给的，`widget.font()` 在某些控件上
    还没同步过来（实测构造时量到 24px、实际渲染字体下 4 个空格是 28px）。
    `QTextDocument.defaultFont()` 跟着控件字体走，也允许调用方显式指定（用例就是这么测的）。
    """
    document = getattr(widget, "document", None)
    if callable(document):
        font = document().defaultFont()
        if font is not None and (font.pointSizeF() > 0 or font.pixelSize() > 0):
            return font
    return widget.font()


def apply_shell_tab_stop(widget, spaces: int = TAB_SPACES) -> float:
    """把 `widget` 的制表位设成 `spaces` 个空格宽，返回设好的像素值（测试与诊断用）。

    **值没变就不设**：`setTabStopDistance()` 会让控件重排一次，而本函数挂在
    `FontChange` 上 —— 主题每应用一次、全局字体一设，所有存活控件都会收到这个事件。
    不加这道判断的话，界面上的每个文本控件都要白重排一遍（实测：整个用例套件明显变慢）。
    """
    from PySide6.QtGui import QFontMetricsF

    metrics = QFontMetricsF(_measure_font(widget))
    width = float(spaces * metrics.horizontalAdvance(" "))
    if abs(widget.tabStopDistance() - width) > 0.01:
        widget.setTabStopDistance(width)
    return width
