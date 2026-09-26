"""带行号的脚本视图（中栏「本轮」页）：可手改 + shell 语法高亮 + 粘贴即规范化。

行号区是 Qt 的经典做法：在 QPlainTextEdit 旁边挂一个独立的 QWidget，宽度随行数
位数变化，滚动时靠 updateRequest 同步。之所以不用「往正文里插行号文本」，是因为
那样会把行号混进脚本内容，复制、跳转、diff 全都要先过滤一遍。

**这一版之前是只读的**，现在允许手改（用户："自动格式化代码，不论我是删除还是粘贴"）：
- 粘贴：先认换行符（CRLF / 裸 CR → LF）并把行首 tab 换成空格，再插入，并**说一句**改了什么；
- 回车：沿用当前行缩进，`then`/`do`/`{` 之后再多一档（VS Code 那样的自动缩进）；
- 「格式化」按钮 / `Ctrl+Shift+F`：整篇按结构重排缩进（见 `shell_toolchain/format.py`）。

**执行权没有变化**：手里的文本仍然要过 shellcheck、再经人工确认才会被 bash 执行
（`改后重跑` 那条路），界面上能改的只是"要送去校验的文本"。
"""

from __future__ import annotations

import re

from PySide6.QtCore import QRect, QSize, Qt, Signal
from PySide6.QtGui import (
    QFontDatabase, QKeySequence, QPainter, QPaintEvent, QPalette, QResizeEvent, QShortcut,
    QTextCursor,
)
from PySide6.QtWidgets import QPlainTextEdit, QWidget

from ...shell_toolchain.format import format_script, normalize_newlines, tidy_line
from .selection_menu import install_ask_action, selected_text
from .shell_highlight import ShellHighlighter

_RIGHT_PADDING = 8  # 行号与文本之间的呼吸位，贴太紧两位数会挤到正文上
_TAB_WIDTH = 4      # shell 脚本里 tab 按 4 空格显示才对得上缩进


class ScriptView(QPlainTextEdit):
    """带行号的脚本视图（可手改、带高亮）；jump_to_line 供右栏的报告点击跳转用。"""

    # 选中一段后右键「就选中的代码提问」：(选中的代码, 来源说明如「本轮脚本 · 第 3–9 行」)
    ask_about_selection = Signal(str, str)
    # 自动整理的结果说明（粘贴换行符、格式化…）：由中栏转给状态栏，用户得看得见
    notice = Signal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("scriptView")
        # 可手改：执行仍然要过 shellcheck + 人工确认（见模块说明）
        self.setReadOnly(False)
        self.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        # 脚本必须等宽显示：缩进与对齐是 shellcheck 报告里「列」的含义所在
        self.setFont(QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont))
        self.setTabStopDistance(float(_TAB_WIDTH * self.fontMetrics().horizontalAdvance(" ")))

        self._area = _LineNumberArea(self)
        # 行数一变宽度就可能要变，滚动时行号区也必须跟着重画，否则会错位或留残影
        self.blockCountChanged.connect(lambda _count: self._update_area_width())
        self.updateRequest.connect(self._on_update_request)
        self._update_area_width()

        # 选中 → 右键 →「就选中的代码提问」（用户要求"选中代码进行对话"）。
        # 由控件自己算行号来源：它本来就在管行号区，别让外面再数一遍。
        install_ask_action(self, self._ask_about_selection)
        self.setToolTip(
            "选中代码后右键可「就选中的代码提问」；正文可以直接改，\n"
            "Ctrl+Shift+F 按结构格式化（粘贴时会自动把 CRLF 换成 LF）。"
        )

        # 语法高亮：只重画变化的行，长脚本滚动也不卡
        self.highlighter = ShellHighlighter(self.document())
        self.format_shortcut = QShortcut(QKeySequence("Ctrl+Shift+F"), self)
        self.format_shortcut.activated.connect(self.format_now)

    # ── 对外接口 ──────────────────────────────────────────────────
    def selected_code(self) -> str:
        """选中的代码（新行统一成 `\\n`）；没有选中返回空串。"""
        return selected_text(self)

    def selection_source(self) -> str:
        """选中内容的来源说明：`本轮脚本 · 第 3–9 行`（单行时写 `第 3 行`）。"""
        cursor = self.textCursor()
        if cursor is None or not cursor.hasSelection():
            return ""
        document = self.document()
        start = document.findBlock(cursor.selectionStart()).blockNumber() + 1
        # 在一行行首结束的选区，selectionEnd 落在**下一块**的开头 —— 减一格才是最后一行
        end_position = max(cursor.selectionStart(), cursor.selectionEnd() - 1)
        end = document.findBlock(end_position).blockNumber() + 1
        span = f"第 {start} 行" if start == end else f"第 {start}–{end} 行"
        return f"本轮脚本 · {span}"

    def _ask_about_selection(self, text: str) -> None:
        self.ask_about_selection.emit(text, self.selection_source())

    def set_text(self, text: str) -> None:
        """替换正文并把光标移回第 1 行：新一轮脚本总是从顶部开始读。

        进来先归一下换行符：脚本可能来自 Windows 上的粘贴或 CRLF 仓库，
        `\r` 留在里面会让 shellcheck 与 bash 都报"看着一模一样的行"出错。
        """
        fixed, count = normalize_newlines(text)
        if count:
            self.notice.emit(f"已把 {count} 处 CRLF/CR 换行符换成 LF")
        self.setPlainText(fixed)
        self.jump_to_line(1)

    def format_now(self) -> bool:
        """按结构格式化整篇；返回是否真的改了东西。

        改动会直接落到界面上（用户看到的就是将要被校验、被执行的那份文本），
        并把改动说明发出去 —— "自动格式化"如果只在内部发生，用户下次会以为脚本被改坏了。
        """
        outcome = format_script(self.toPlainText())
        if not outcome.changed:
            self.notice.emit("脚本已经是整理过的样子，没有改动")
            return False
        cursor_line = self.textCursor().blockNumber()
        self.setPlainText(outcome.text)
        self.jump_to_line(cursor_line + 1)      # 尽量停在原来那一行，别把人甩到文件头
        self.notice.emit("已格式化脚本：" + "；".join(outcome.notes))
        return True

    def insertFromMimeData(self, source) -> None:  # noqa: N802 - Qt 命名
        """粘贴：先认换行符、再整理每一行的行首（tab → 空格）与行尾空白，然后插入。

        用户的说法是"不论我是删除还是粘贴都自动识别换行符号"，所以这里不是"能贴就行"：
        贴进来的是**可执行文本**，`\r`、行首 tab、行尾空格都会在 shellcheck 与 bash 那边
        制造"看起来一样却报错"的行。
        """
        text = source.text() if source is not None else ""
        if not text:
            super().insertFromMimeData(source)
            return
        fixed, count = normalize_newlines(text)
        tidied = "\n".join(tidy_line(line) for line in fixed.split("\n"))
        super().insertPlainText(tidied)
        if count:
            self.notice.emit(f"粘贴时把 {count} 处 CRLF/CR 换行符换成 LF")

    def keyPressEvent(self, event) -> None:  # noqa: N802 - Qt 命名
        """回车自动缩进：沿用当前行缩进，`then`/`do`/`{` 之后再进一档。"""
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter) and not (
            event.modifiers()
            & (
                Qt.KeyboardModifier.ControlModifier
                | Qt.KeyboardModifier.ShiftModifier
                | Qt.KeyboardModifier.AltModifier
            )
        ):
            self._insert_indented_line()
            return
        super().keyPressEvent(event)

    def _insert_indented_line(self) -> None:
        cursor = self.textCursor()
        line = cursor.block().text()
        stripped = line.lstrip(" \t")
        body = stripped.split("#", 1)[0].rstrip()
        indent = line[: len(line) - len(stripped)]
        # 缩进单位跟着当前行走（用 tab 的人不该被塞空格：那样会与既有缩进不一致）
        unit = "\t" if "\t" in indent else "    "
        if body.endswith(("then", "do", "{", "(")) or re.search(r"\b(do|then)\s*$", body):
            indent += unit
        cursor.insertText("\n" + indent)
        self.setTextCursor(cursor)

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
