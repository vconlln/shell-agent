"""把 Markdown 渲染成界面里的富文本 —— 全应用只有这一个入口。

**为什么用 Qt 自带的解析，而不是引 python-markdown 之类**：`QTextDocument.setMarkdown()` 与
`QLabel(textFormat=MarkdownText)` 是 Qt 自带能力（本机 PySide6 6.11 实测支持标题、粗斜体、
行内代码、围栏代码、有序/无序列表、引用、分隔线、GFM 表格）。自己再引一个 Markdown 库，
打包（PyInstaller 的 hidden import / datas）与 Windows 那边都要跟着改，还得跟它的依赖树；
Qt 这套零依赖、离线可用。

**为什么要自己再上一遍样式**：Qt 解析完的文档**不吃** `setDefaultStyleSheet`
（实测：`setDefaultStyleSheet` + `setMarkdown` 之后，`h1` 没有字号、`pre` 没有底色、
表格是默认黑框）—— 在深色主题里那套默认样式几乎看不出层级，用户的原话是
"看起来特别不好看"。所以解析完再走一遍 `theme_document()`：按主题的颜色与字号把标题、
代码块、行内代码、表格重新着色（HTML 那条路 `setHtml` 才吃样式表 —— 真要走那条路时，
样式得重写一份，这里不做没有调用方的备用实现）。
"""

from __future__ import annotations

from PySide6.QtCore import QEvent, Qt
from PySide6.QtGui import (
    QColor,
    QFont,
    QTextCharFormat,
    QTextDocument,
    QTextTable,
    QTextTableCellFormat,
)
from PySide6.QtWidgets import QApplication, QLabel, QTextBrowser, QWidget

from . import theme

# 标题层级对应的字号比例（相对正文字号）。Qt 解析 Markdown 时给标题的是 `xx-large`
# 这类相对关键字，比例不可控、在不同字体上也不同；这里按主题字号自己算，各档一致。
_HEADING_RATIOS = {1: 1.45, 2: 1.28, 3: 1.14, 4: 1.06, 5: 1.0, 6: 1.0}


def base_point_size() -> float:
    """当前界面基准字号（pt）：跟随主题的 `ui_scale`（`apply_theme` 会设 app 字体）。"""
    app = QApplication.instance()
    size = float(app.font().pointSizeF()) if app is not None else 0.0
    return size if size > 0 else 9.75


def _is_monospace(format: QTextCharFormat) -> bool:
    """这一段的字体是不是等宽（Qt 解析 Markdown 就是用等宽字体标记代码的）。

    **不要用 `QTextCharFormat.fontFamily()`**：它在 PySide6 6.11 里已废弃，而且调用它会
    直接把进程打崩（实测：段错误 + 核心转储，连堆栈都没有）。只用 `fontFamilies()`，
    它在"没显式指定字体"时返回 `None`。

    实测 Qt 给代码打的标记：行内代码 `families=['monospace'], fixed=True`；
    围栏代码块 `families=['monospace'], fixed=False`。
    """
    families = format.fontFamilies()
    names: list[str] = []
    if hasattr(families, "toStringList"):
        try:
            names = [str(name) for name in families.toStringList()]
        except TypeError:      # 某些版本这里不是 QStringList
            names = []
    elif isinstance(families, (list, tuple)):
        names = [str(name) for name in families]
    return any("mono" in name.lower() or "courier" in name.lower() for name in names)


def theme_document(document: QTextDocument, base: float = 0.0) -> None:
    """按主题给解析好的文档上一次色：标题字号、代码底色、表格边框。

    识别方式是 Qt 解析 Markdown 留下的**结构信息**（不是猜文本）：
    - 标题：`QTextBlockFormat.headingLevel()`；
    - 围栏代码块：整块都是等宽字体（且不是空行）→ 给块底色与内边距；
    - 行内代码：只有**片段**是等宽字体 → 给片段底色；
    - 表格：`document.rootFrame()` 里的 `QTextTable` → 统一边框、单元格内边距，表头加底色。

    **两遍走，中途不持有任何块/片段句柄** —— 这是踩过坑的写法：一边遍历 `QTextFragment`
    一边改字符格式，改完手里那个片段对象就已经失效了（Qt 在改格式时会重排文档），
    再拿它去 `position()` 会直接把进程打崩（实测：段错误 + 核心转储）。
    所以第一遍只**读**（把位置与长度记成普通整数），第二遍按块号重新取游标逐条改，
    并整体包在一个 `beginEditBlock/endEditBlock` 里（少重排几次，也不闪）。
    """
    base = base or base_point_size()
    code_bg = QColor(theme.css("bg_under"))
    border = QColor(theme.css("border_light"))

    headings: list[tuple[int, int]] = []          # (块号, 级别)
    code_blocks: list[int] = []                   # 整块等宽的块号
    inline_code: list[tuple[int, int]] = []       # 行内代码的 (起始位置, 长度)
    block = document.begin()
    while block.isValid():
        number = block.blockNumber()
        level = int(block.blockFormat().headingLevel() or 0)
        if level:
            headings.append((number, level))
        spans = [
            (fragment.position(), fragment.length(), _is_monospace(fragment.charFormat()), fragment.text())
            for fragment in _fragments(block)
        ]
        text = block.text()
        if spans and all(item[2] for item in spans) and text.strip():
            code_blocks.append(number)
        else:
            inline_code.extend(
                (position, length) for position, length, mono, body in spans if mono and body.strip()
            )
        block = block.next()

    from PySide6.QtGui import QTextCursor

    cursor = QTextCursor(document)
    cursor.beginEditBlock()
    try:
        for number, level in headings:
            block = document.findBlockByNumber(number)
            if not block.isValid():
                continue
            block_cursor = QTextCursor(block)
            char_format = QTextCharFormat()
            char_format.setFontPointSize(round(base * _HEADING_RATIOS.get(level, 1.0), 2))
            char_format.setFontWeight(QFont.Weight.DemiBold)
            block_cursor.select(QTextCursor.SelectionType.BlockUnderCursor)
            block_cursor.mergeCharFormat(char_format)
            block_format = block.blockFormat()
            block_format.setTopMargin(10 if level <= 2 else 7)
            block_format.setBottomMargin(5)
            block_cursor.setBlockFormat(block_format)

        for number in code_blocks:
            block = document.findBlockByNumber(number)
            if not block.isValid():
                continue
            block_cursor = QTextCursor(block)
            block_format = block.blockFormat()
            block_format.setBackground(code_bg)
            block_format.setTopMargin(3)
            block_format.setBottomMargin(3)
            block_format.setLeftMargin(8)
            block_format.setRightMargin(8)
            block_cursor.setBlockFormat(block_format)

        inline_format = QTextCharFormat()
        inline_format.setBackground(code_bg)
        for position, length in inline_code:
            cursor.setPosition(position)
            cursor.setPosition(position + length, QTextCursor.MoveMode.KeepAnchor)
            cursor.mergeCharFormat(inline_format)
    finally:
        cursor.endEditBlock()

    for frame in document.rootFrame().childFrames():
        if not isinstance(frame, QTextTable):
            continue
        table_format = frame.format()
        table_format.setBorder(1)
        table_format.setBorderBrush(border)
        table_format.setCellPadding(5)
        table_format.setCellSpacing(0)
        frame.setFormat(table_format)
        for row in range(frame.rows()):
            for column in range(frame.columns()):
                cell = frame.cellAt(row, column)
                # 单元格的内边距属于 `QTextTableCellFormat`（`cell.format()` 拿到的是
                # 基类 `QTextCharFormat`，上面没有 setPadding —— 实测直接 AttributeError）
                cell_format = QTextTableCellFormat()
                cell_format.setPadding(5)
                if row == 0 and frame.rows() > 1:       # 表头：Markdown 的表头就是第一行
                    cell_format.setBackground(code_bg)
                    cell_format.setFontWeight(QFont.Weight.DemiBold)
                cell.setFormat(cell_format)


def _fragments(block):
    """只读地列出块里的片段（调用方**不许**在之后再使用这些片段对象，见 `theme_document`）。"""
    iterator = block.begin()
    while not iterator.atEnd():
        fragment = iterator.fragment()
        if fragment.isValid():
            yield fragment
        iterator += 1


class MarkdownBrowser(QTextBrowser):
    """只读的 Markdown 视图：`set_markdown(text)` 渲染，`markdown()` 取回源文。

    自带滚动条（`QTextBrowser` 本来就是滚动的），所以不需要外层再套滚动区 ——
    方案文档、报告说明这类长度不可控的文本正需要它。
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setOpenExternalLinks(True)      # 方案里的链接点得开
        self.setReadOnly(True)
        self.setLineWrapMode(QTextBrowser.LineWrapMode.WidgetWidth)
        # 视口自己别上色：`QAbstractScrollArea` 的视口按调色板 Base 画，
        # 不关掉它的话主题里给 `#planPreview` 写的那层底色根本露不出来
        # （实测：窗口里方案预览与旁边的空白一个颜色，看起来像"没有边框的文本框"）。
        self.viewport().setObjectName("markdownViewport")
        self.viewport().setAutoFillBackground(False)
        self._source = ""

    def set_markdown(self, text: str) -> None:
        """渲染 Markdown；空串就清空。**源文留一份**，字体变了要按新字号重渲染。"""
        self._source = text or ""
        self._render()

    def markdown(self) -> str:
        return self._source

    def set_plain(self, text: str) -> None:
        """显示**不渲染**的纯文本（空态提示、读取失败的原因这类我们自己写的话）。

        这些句子里的 `*`、`_`、反引号是普通字符，拿去渲染会被吃掉 ——
        所以要有一条明确"别渲染"的路，而不是让调用方自己转义。
        """
        self._source = ""
        self.setPlainText(text or "")

    def _render(self) -> None:
        self.setMarkdown(self._source)
        theme_document(self.document())

    def changeEvent(self, event) -> None:  # noqa: N802 - Qt 命名
        """字体变了（换主题 / 改 ui_scale）就按新字号重渲染。

        不重渲染的话标题层级会一直停在旧字号上：用户把缩放从 100% 调到 150%，
        其它控件都变大了，方案预览里的标题还是 100% 那一档。
        """
        super().changeEvent(event)
        if event.type() == QEvent.Type.FontChange and self._source:
            self._render()


def use_markdown_label(label: QLabel) -> QLabel:
    """把 `QLabel` 切成 Markdown 模式（一句话的说明也常带 `**加粗**` 与反引号）。

    `QLabel` 的 Markdown 模式由 Qt 自己排版，识别不出我们那套"代码块底色"，
    所以只用在**正文**这类短文本上；长文档一律走 `MarkdownBrowser`。
    """
    label.setTextFormat(Qt.TextFormat.MarkdownText)
    return label
