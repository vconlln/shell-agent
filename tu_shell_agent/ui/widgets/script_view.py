"""带行号的只读脚本视图（中栏「本轮」页）。

行号区是 Qt 的经典做法：在 QPlainTextEdit 旁边挂一个独立的 QWidget，宽度随行数
位数变化，滚动时靠 updateRequest 同步。之所以不用「往正文里插行号文本」，是因为
那样会把行号混进脚本内容，复制、跳转、diff 全都要先过滤一遍。
"""

from __future__ import annotations

from PySide6.QtCore import QRect, QSize, Qt
from PySide6.QtGui import (
    QFontDatabase, QPainter, QPaintEvent, QPalette, QResizeEvent, QTextCursor,
)
from PySide6.QtWidgets import QPlainTextEdit, QWidget

_RIGHT_PADDING = 8  # 行号与文本之间的呼吸位，贴太紧两位数会挤到正文上
_TAB_WIDTH = 4      # shell 脚本里 tab 按 4 空格显示才对得上缩进


class ScriptView(QPlainTextEdit):
    """带行号的只读脚本视图；jump_to_line 供右栏的报告点击跳转用。"""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("scriptView")
        self.setReadOnly(True)
        self.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        # 脚本必须等宽显示：缩进与对齐是 shellcheck 报告里「列」的含义所在
        self.setFont(QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont))
        self.setTabStopDistance(float(_TAB_WIDTH * self.fontMetrics().horizontalAdvance(" ")))

        self._area = _LineNumberArea(self)
        # 行数一变宽度就可能要变，滚动时行号区也必须跟着重画，否则会错位或留残影
        self.blockCountChanged.connect(lambda _count: self._update_area_width())
        self.updateRequest.connect(self._on_update_request)
        self._update_area_width()

    # ── 对外接口 ──────────────────────────────────────────────────
    def set_text(self, text: str) -> None:
        """替换正文并把光标移回第 1 行：新一轮脚本总是从顶部开始读。"""
        self.setPlainText(text)
        self.jump_to_line(1)

    def jump_to_line(self, line_no: int) -> None:
        """跳到 1-based 行号（越界夹到文档首尾），并把该行居中。

        高位数必须也夹住：findBlockByNumber 越界返回的是无效块，用它构造的游标
        会把视图悄悄留在原地——右栏报告里的行号一旦比当前脚本长（例如上一轮的
        报告），看起来就像「点击没反应」。
        """
        last = max(self.blockCount() - 1, 0)
        index = min(max(line_no - 1, 0), last)
        block = self.document().findBlockByNumber(index)
        cursor = QTextCursor(block)
        self.setTextCursor(cursor)
        self.centerCursor()

    def line_number_area_width(self) -> int:
        """行号区宽度：按当前最大行号的位数算，至少留两位的余量。"""
        digits = max(2, len(str(max(1, self.blockCount()))))
        return _RIGHT_PADDING + self.fontMetrics().horizontalAdvance("9") * digits

    def paint_line_numbers(self, event: QPaintEvent) -> None:
        """只画可视区域内的行号：QPlainTextEdit 的几何是按块算的，逐块累加即可。"""
        painter = QPainter(self._area)
        palette = self.palette()
        painter.fillRect(event.rect(), palette.color(QPalette.ColorRole.AlternateBase))
        painter.setPen(palette.color(QPalette.ColorRole.PlaceholderText))

        block = self.firstVisibleBlock()
        block_number = block.blockNumber()
        offset = self.contentOffset()
        top = round(self.blockBoundingGeometry(block).translated(offset).top())
        bottom = top + round(self.blockBoundingRect(block).height())
        line_height = self.fontMetrics().height()
        width = self._area.width() - _RIGHT_PADDING

        while block.isValid() and top <= event.rect().bottom():
            if block.isVisible() and bottom >= event.rect().top():
                painter.drawText(
                    0, top, width, line_height,
                    int(Qt.AlignmentFlag.AlignRight), str(block_number + 1),
                )
            block = block.next()
            top = bottom
            bottom = top + round(self.blockBoundingRect(block).height())
            block_number += 1

    # ── Qt 钩子 ───────────────────────────────────────────────────
    def resizeEvent(self, event: QResizeEvent) -> None:  # noqa: N802 - Qt 命名
        super().resizeEvent(event)
        self._update_area_geometry()

    def _on_update_request(self, rect: QRect, dy: int) -> None:
        """Qt 要求的两条分支：整块滚动直接搬像素，局部重绘才重画行号。"""
        if dy:
            self._area.scroll(0, dy)
        else:
            self._area.update(0, rect.y(), self._area.width(), rect.height())
        if rect.contains(self.viewport().rect()):
            self._update_area_width()

    # ── 行号区维护 ────────────────────────────────────────────────
    def _update_area_width(self) -> None:
        self.setViewportMargins(self.line_number_area_width(), 0, 0, 0)
        self._update_area_geometry()

    def _update_area_geometry(self) -> None:
        rect = self.contentsRect()
        self._area.setGeometry(
            QRect(rect.left(), rect.top(), self.line_number_area_width(), rect.height())
        )


class _LineNumberArea(QWidget):
    """行号区的宿主：绘制与宽度都委托回 ScriptView，避免两处各算一遍行高。"""

    def __init__(self, editor: ScriptView) -> None:
        super().__init__(editor)
        self._editor = editor

    def sizeHint(self) -> QSize:  # noqa: N802 - Qt 命名
        return QSize(self._editor.line_number_area_width(), 0)

    def paintEvent(self, event: QPaintEvent) -> None:  # noqa: N802 - Qt 命名
        self._editor.paint_line_numbers(event)
