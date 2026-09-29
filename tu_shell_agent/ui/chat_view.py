"""对话记录区的呈现：**每一轮一块**（用户块 / 模型回复块 / 活动行 / 时间与复制）。

**为什么要重做**（用户 2026-09-20 的截图与要求）：之前的记录区是一个 `QPlainTextEdit`，
所有轮次用纯文本拼在一起（`你：…`、`── 模型回复 ──`、`模型：…`）。一轮轮攒下来就是一大片
连着的文字 —— 用户的原话是"每一轮会话都在一块，看不清楚"，并贴了 DSH 的对话界面作参照。
现在每一轮是一张卡片：用户消息与模型回复**各自成块**（圆角、不同底色），卡里带工具活动行与
右下角的时间 + 复制按钮，轮与轮之间留白。

**保留的契约**（这些是别的模块依赖的，不能因为换了呈现方式就丢）：

- `toPlainText()` 仍给出与旧版等价的纯文本 —— 引擎那侧靠它抠脚本块
  （`extract_last_script(chat.transcript_text())`），"把最新脚本放进中栏"这条路因此一行未改；
- 正文仍然**可选可复制**（`QLabel` 打开文本选择），并且能"就选中的内容提问"
  （右键菜单，见 `widgets/selection_menu.py`）—— 用户明确要求过"选中代码进行对话"。

**不做的两件事**（写明，免得下次被当成缺陷）：

1. 不解析 Markdown 语法（粗体/列表/表格）。这一路的回复主体是脚本，所以只把**围栏代码块**
   单独块出来（等宽 + 复制按钮）——再多就是自己写一个 Markdown 渲染器，收益不成比例。
2. 流式期间不做分块渲染：增量文本先进同一个正文块，`finish_reply()` 时才切成"散文 + 代码块"。
   每个 delta 都重排整轮会把流式输出的顺滑感吃掉（几十毫秒一次的重排很贵）。

**代价（如实写下来）**：一个轮次一个控件树，比"一个文本控件装满字"重。对话的长度由人决定
（一问一轮），正常会话几十轮，这个量级没问题；真到了几百轮该做的是"只保留最近 N 轮 + 更早的
折起来"，而不是在这里偷偷丢弃内容 —— 丢弃会让"复制/抠脚本"看到的与屏幕上看到的不一致。
"""

from __future__ import annotations

import re
import time
from collections.abc import Callable

from PySide6.QtCore import QEvent, Qt, QTimer

from PySide6.QtGui import QFontDatabase, QTextOption
from PySide6.QtWidgets import (
    QApplication,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

# 围栏代码块：```lang\n…```；**允许未闭合**（流式输出到一半就是这个形状）
_FENCE = re.compile(r"```([a-zA-Z0-9_+.-]*)[ \t]*\n(.*?)(?:```|\Z)", re.S)

# 用户消息里的引用头（`关于以下引用内容…`）在卡片里不必再占满整段：显示时收成一行摘要，
# 但**发给模型的正文一个字符都不动**（原样在上面那句话里，见 compose_message）。
_QUOTE_MARK = "关于以下引用内容"

# 这些语言标记下的代码块按 shell 上色；空标记也算（引擎那条流发的就是没有围栏的 script）
_SHELL_LANGUAGES = frozenset({"", "bash", "sh", "shell", "zsh", "ksh", "console", "shell-session"})

# 题面契约的标记（与引擎那一侧同一个来源，不各写一份字面量）
from ..orchestrator.cli_contract import (  # noqa: E402 - 见上方说明
    ASSUMPTIONS_BEGIN,
    END as CONTRACT_END,
    NOTES_BEGIN,
    SCRIPT_BEGIN,
)

_CONTRACT_MARKERS = (SCRIPT_BEGIN, NOTES_BEGIN, ASSUMPTIONS_BEGIN, CONTRACT_END, "@@TU:BODY@@")

# 像代码的行：注释、赋值、控制关键字、函数定义、明显的 shell 记号
_CODE_LINE = re.compile(
    r"^\s*(?:#|export\s|readonly\s|local\s|declare\s|if\s|then\b|elif\s|else\b|fi\b|"
    r"for\s|while\s|until\s|do\b|done\b|case\s|esac\b|function\s|\}\s*$|\)\s*$|"
    r"[A-Za-z_][A-Za-z0-9_]*\s*\(\s*\)|[A-Za-z_][A-Za-z0-9_]*=)"
)
_SHELL_MARKS = ("$(", "${", "&&", "||", "2>&1", ">/dev/null", "fi\n", "; then", " ]", "[[")


def looks_like_code(text: str) -> bool:
    """这段模型输出是"在说话"还是"在写代码/契约"。

    **判错方向的代价不对称**：判成代码最多是显示得朴素（等宽、不渲染），
    判成 Markdown 会把脚本里的 `# 注释` 变成巨型标题、把相邻的代码行并成一个段落
    （用户截图里就是这样：满屏加粗大字 + 代码被压成一坨）。

    所以判据宽进严出：**只有明确像代码时才说"是代码"**，
    但一旦命中契约标记（`===TU-SCRIPT===` 那一套）或 shebang，就直接判定。
    """
    body = (text or "").strip()
    if not body:
        return False
    if any(marker in body for marker in _CONTRACT_MARKERS):
        return True
    lines = [line for line in body.splitlines() if line.strip()]
    if not lines:
        return False
    if lines[0].startswith("#!"):
        return True
    shellish = sum(
        1
        for line in lines
        if _CODE_LINE.match(line) or any(mark in line for mark in _SHELL_MARKS)
    )
    # 多数行都像代码，就按代码显示（纯散文很难凑出这个比例）
    return shellish >= max(2, int(len(lines) * 0.5))


def split_contract(text: str) -> list[tuple[str, str]]:
    """把题面契约拆成 `[("code", 脚本), ("prose", 取舍说明), ("prose", 假设)]`。

    引擎那条流（"第 N 轮 · 模型输出"）发的就是契约本身：脚本是**代码**，
    取舍说明与假设是**散文**。混在一起渲染的结果用户已经给过截图了 ——
    脚本的 `#` 注释变成巨型标题。拆开之后：脚本进代码块（等宽 + 复制），
    说明进正文（按 Markdown 渲染）。
    """
    if SCRIPT_BEGIN not in text:
        return []
    sections: list[tuple[str, str]] = []
    script = text.split(SCRIPT_BEGIN, 1)[1]
    for marker in (NOTES_BEGIN, ASSUMPTIONS_BEGIN, CONTRACT_END):
        script = script.split(marker, 1)[0]
    if script.strip():
        sections.append(("code", script.strip("\n")))
    for marker, title in ((NOTES_BEGIN, "取舍说明"), (ASSUMPTIONS_BEGIN, "假设")):
        if marker not in text:
            continue
        chunk = text.split(marker, 1)[1].split(CONTRACT_END, 1)[0]
        for other in (NOTES_BEGIN, ASSUMPTIONS_BEGIN, SCRIPT_BEGIN):
            chunk = chunk.split(other, 1)[0]
        if chunk.strip():
            sections.append(("prose", f"**{title}**\n\n{chunk.strip()}"))
    return sections

# 思考过程的分节标记：与 `api_client.THINKING_HEADER` 是同一个字符串（那边是唯一来源，
# 这里 import 过来用，免得两处字面量各写一遍后漂移）
from ..agent_backends.api_client import THINKING_HEADER  # noqa: E402 - 见上方说明
from .markdown import use_markdown_label
from .widgets.shell_highlight import ShellHighlighter
from .widgets.defer import schedule_after_event_loop
from .widgets.tabstop import TAB_SPACES, apply_shell_tab_stop


def split_segments(text: str) -> list[tuple[str, str]]:
    """把正文切成 `(类型, 内容)`；类型是 `"text"` 或 `"code"`。

    代码块带语言标记时把它留在内容第一行吗？不留 —— 语言名由块头单独显示（见 `_code_block`），
    内容保持"能直接选中复制走"的纯净形态（用户复制一段脚本时不想把 `bash` 也复制进去）。
    """
    segments: list[tuple[str, str]] = []
    position = 0
    for match in _FENCE.finditer(text):
        before = text[position : match.start()]
        if before.strip():
            segments.append(("text", before.strip("\n")))
        language = match.group(1).strip()
        body = match.group(2)
        if language:
            segments.append(("lang", language))
        segments.append(("code", body.rstrip("\n")))
        position = match.end()
    tail = text[position:]
    if tail.strip():
        segments.append(("text", tail.strip("\n")))
    if not segments:
        segments.append(("text", text))
    return segments


def _readable(text: str) -> str:
    """卡片上显示的正文：把引用头收成一行摘要（看不到自己发了什么同样会让人困惑）。"""
    if _QUOTE_MARK not in text:
        return text
    head, _, rest = text.partition("\n\n")
    if rest and _QUOTE_MARK in head:
        lines = [line for line in head.splitlines() if line.strip()]
        label = lines[0].strip() if lines else _QUOTE_MARK
        return f"{label}（引用内容见消息全文）\n\n{rest.strip()}"
    return text


class CodeView(QPlainTextEdit):
    """代码块正文：等宽、只读、**按宽度自动换行并自动长高**。

    为什么不是 `QLabel`：`QLabel` 的换行只在空格处断，而 shell 脚本里恰恰有一堆没有空格的
    长串（`backup="/tmp/logs-$(date +%s).tar.gz"`）—— 那种行根本断不开，只能被裁掉半个
    （实测第一版就是这样，用户看到的是"脚本缺了一半"）。`QPlainTextEdit` 能设置
    `WrapAtWordBoundaryOrAnywhere`：有空格按空格断，没空格按字符断，一个字都不丢。

    自动长高 + 关掉自己的滚动条：它长在外层滚动区里，内部再滚一层会出现"滚动条套滚动条"，
    鼠标滚轮到底该滚谁也就说不清了。选中/复制/右键菜单都是现成的（`QLabel` 还得自己补菜单）。
    """

    def __init__(self, text: str, language: str = "", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("chatCodeText")
        self._language = (language or "").strip().lower()
        self.setPlainText(text)
        self.setReadOnly(True)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setLineWrapMode(QPlainTextEdit.LineWrapMode.WidgetWidth)
        self.setWordWrapMode(QTextOption.WrapMode.WrapAtWordBoundaryOrAnywhere)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setFocusPolicy(Qt.FocusPolicy.ClickFocus)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.document().setDocumentMargin(0)
        # 制表位 4 个空格（Qt 默认 8 个字符宽，tab 缩进会宽一倍）。
        # 字体由 QSS 给、polish 之后才生效，所以构造时设一次、字体变化时再设一次。
        self.setFont(QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont))
        apply_shell_tab_stop(self)
        # 高度跟着**排版后的内容高度**走。不能用 `documentSizeChanged` 报的尺寸：
        # 实测它给的是 186（真值 114），代码块底部因此空出三行（抓图里一眼可见）。
        # 逐块累加 `blockBoundingRect` 才是"折行之后到底占多高"的准确答案。
        # shell 语法高亮：与中栏脚本视图同一套配色（用户："会话输出的代码没有渲染"）。
        # 只在 shell-ish 的代码块上挂：别的语言（python/json…）按 shell 上色会误导。
        if self._language in _SHELL_LANGUAGES:
            self.highlighter = ShellHighlighter(self.document())
        self.document().contentsChanged.connect(self._reflow)
        QTimer.singleShot(0, self._reflow)

    def resizeEvent(self, event) -> None:  # noqa: N802 - Qt 命名
        """宽度变了要重算高度 —— 但**延到事件循环下一拍**。

        `resizeEvent` 会在一大串场合被触发，其中包括 Qt 应用样式表时的整树重抛光。
        在那里面 `setFixedHeight()`（改几何）等于在 Qt 遍历控件树的中途改结构：
        实测整轮用例会随机段错误，C 栈落在 libQt6Widgets、Python 栈落在
        `apply_theme` 的 `setStyle()` 里。延一拍观感无差别，却把两件事分开了。
        """
        super().resizeEvent(event)
        schedule_after_event_loop(self, self._reflow)

    def showEvent(self, event) -> None:  # noqa: N802 - Qt 命名
        """显示时再算一次：刚插进布局的那一刻宽度还没定（否则第一帧是 640x480 的默认块）。

        同样延一拍（原因见 `resizeEvent`）：show 也可能发生在整树重抛光的过程中。
        """
        super().showEvent(event)
        schedule_after_event_loop(self, self._refresh_metrics)

    def changeEvent(self, event) -> None:  # noqa: N802 - Qt 命名
        """字体变了：制表位与高度都要重算 —— 但**延到事件循环下一拍**再做。

        在 `changeEvent`（Qt 的 polish / 样式应用过程中）里改 `setFixedHeight`
        等于在 Qt 遍历控件树的中途改几何，实测在"恢复样式表"那种整树重抛光时会把进程打崩
        （C 栈落在 libQt6Widgets）。延一拍既不影响观感，也不给 Qt 制造这种时机。
        """
        super().changeEvent(event)
        if event.type() == QEvent.Type.FontChange:
            schedule_after_event_loop(self, self._refresh_metrics)

    def _refresh_metrics(self) -> None:
        apply_shell_tab_stop(self)
        self._reflow()

    def _reflow(self) -> None:
        """先按视口宽度排版，再按排版结果定高（宽度变了折行就变，所以两步必须一起做）。"""
        width = self.viewport().width()
        if width > 0 and abs(self.document().textWidth() - width) > 0.5:
            self.document().setTextWidth(width)
        margins = self.contentsMargins()
        wanted = self._content_height() + margins.top() + margins.bottom()
        if wanted > 0 and wanted != self.height():
            self.setFixedHeight(wanted)

    def _content_height(self) -> int:
        document = self.document()
        layout = document.documentLayout()
        total = 0.0
        block = document.begin()
        while block.isValid():
            total += layout.blockBoundingRect(block).height()
            block = block.next()
        return int(total) + 2


class _CopyButton(QPushButton):
    """复制按钮：点一下把**这一块**的文本放进剪贴板，按钮上给一句短反馈。"""

    def __init__(self, text_getter: Callable[[], str], *, tip: str = "复制", parent=None) -> None:
        super().__init__("⧉", parent)
        self.setObjectName("chatCopyButton")
        self.setToolTip(tip)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setFixedSize(24, 24)
        self._text_getter = text_getter
        self.clicked.connect(self._copy)

    def _copy(self) -> None:
        clipboard = QApplication.clipboard()
        if clipboard is None:
            return
        clipboard.setText(self._text_getter())
        self.setText("✓")
        # 1.2 秒后还原：短到不打扰，长到能看见
        QTimer.singleShot(1200, lambda: self.setText("⧉"))


def _selectable_label(text: str, object_name: str, *, markdown: bool = False) -> QLabel:
    """可选中的正文标签（选中 → 右键「就选中的内容提问」，与记录区旧行为一致）。

    `markdown=True` 时按 Markdown 渲染（模型说的话常常带 `**加粗**`、列表、行内代码）。
    **用户自己敲的字不渲染**（`markdown=False`）：他写的 `2 * 3`、`_变量_`、`a_b_c` 被当成
    语法吃掉才是真的难用 —— 渲染别人的输出、原样显示用户的输入。
    """
    label = QLabel(text)
    label.setObjectName(object_name)
    label.setWordWrap(True)
    if markdown:
        # `QLabel` 的 Markdown 模式没法定制表位（Qt 默认按 8 个字符宽），
        # 所以这里把 tab 展开成 4 个空格再交给它渲染（用户："会话栏……同样的 Tab 有点长"）。
        # 行首的 tab 展开成 4 空格会被 Markdown 认成缩进代码块 —— 对 shell 片段来说正合适。
        label.setText(text.replace("\t", " " * TAB_SPACES))
        use_markdown_label(label)
    else:
        label.setTextFormat(Qt.TextFormat.PlainText)
    label.setTextInteractionFlags(
        Qt.TextInteractionFlag.TextSelectableByMouse | Qt.TextInteractionFlag.TextSelectableByKeyboard
    )
    label.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Minimum)
    return label


class TurnView(QFrame):
    """一轮对话：用户块 + 模型回复块 + 活动行 + 页脚（时间 / 复制）。"""

    def __init__(self, on_ask: Callable[[str], None] | None = None, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("chatTurn")
        self._on_ask = on_ask
        self._when = time.strftime("%H:%M")

        self.user_block = QFrame()
        self.user_block.setObjectName("chatUserBlock")
        user_layout = QVBoxLayout(self.user_block)
        user_layout.setContentsMargins(10, 8, 10, 8)
        user_layout.setSpacing(2)
        self.user_label = _selectable_label("", "chatUserText")
        self.user_label.setVisible(False)
        user_layout.addWidget(self.user_label)
        self.user_block.setVisible(False)

        # ── 思考过程（可折叠）─────────────────────────────────────────
        # 用户要求："模型对话框模型的思考过程你加一个可以折叠和展开"。
        # 展开态：流式期间默认展开（看得见它在想什么，这也是"直连 API 而不是 CLI"的卖点）；
        # 只要开始写正文就自动收起，让答案占住视线；用户点标题行随时能再展开。
        self.thinking = QWidget()
        self.thinking.setObjectName("chatThinking")
        thinking_layout = QVBoxLayout(self.thinking)
        thinking_layout.setContentsMargins(0, 0, 0, 0)
        thinking_layout.setSpacing(4)
        self.thinking_header = QPushButton()
        self.thinking_header.setObjectName("chatThinkingHeader")
        self.thinking_header.setCursor(Qt.CursorShape.PointingHandCursor)
        self.thinking_header.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.thinking_header.clicked.connect(lambda _checked=False: self.toggle_thinking())
        self.thinking_label = _selectable_label("", "chatThinkingText")
        self.thinking_label.setVisible(False)
        thinking_layout.addWidget(self.thinking_header)
        thinking_layout.addWidget(self.thinking_label)
        self.thinking.setVisible(False)
        self._thinking_text = ""
        self._thinking_open = True

        self.reply_header = QLabel("—— 模型回复 ——")
        self.reply_header.setObjectName("chatReplyHeader")
        self.reply_header.setVisible(False)
        self.reply_body = QWidget()
        self.reply_body.setObjectName("chatReplyBody")
        self.reply_layout = QVBoxLayout(self.reply_body)
        self.reply_layout.setContentsMargins(0, 0, 0, 0)
        self.reply_layout.setSpacing(6)
        self.reply_body.setVisible(False)

        self.activities = QWidget()
        self.activities.setObjectName("chatActivities")
        self.activity_layout = QVBoxLayout(self.activities)
        self.activity_layout.setContentsMargins(0, 0, 0, 0)
        self.activity_layout.setSpacing(2)
        self.activities.setVisible(False)

        self.footer = QWidget()
        self.footer.setObjectName("chatTurnFooter")
        footer_layout = QHBoxLayout(self.footer)
        footer_layout.setContentsMargins(0, 0, 0, 0)
        footer_layout.setSpacing(6)
        self.time_label = QLabel(self._when)
        self.time_label.setObjectName("chatTurnTime")
        self.copy_button = _CopyButton(self.plain_text, tip="复制这一轮的全文")
        footer_layout.addStretch(1)
        footer_layout.addWidget(self.time_label)
        footer_layout.addWidget(self.copy_button)
        self.footer.setVisible(False)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        layout.addWidget(self.user_block)
        layout.addWidget(self.thinking)
        layout.addWidget(self.reply_header)
        layout.addWidget(self.reply_body)
        layout.addWidget(self.activities)
        layout.addWidget(self.footer)

        # 流式期间正文长在同一个标签里，收尾时再切成分块渲染（见模块开头第 2 条）
        self._stream_label: QLabel | None = None
        self._reply_text = ""
        self._segmented = False
        # "有没有用户消息"用**旗标**记，不用 isVisible()：面板没显示（在别的页签里、
        # 或用例里没 show()）时 isVisible() 恒为 False，拿它当状态会把消息当成不存在
        # —— 纯文本回读因此丢过整条用户消息（`_has_proposal` 那条注释里踩过同样的坑）。
        self._has_user = False
        # 卡片上显示 `你：…`（参照图的形状），纯文本回读用**原文**（`_user_text`）——
        # 显示用带前缀的那一份，两者混用会把前缀叠成两遍。
        self._user_text = ""

    # ── 写入 ─────────────────────────────────────────────────────────────
    def set_user(self, text: str, when: str = "") -> None:
        self._has_user = True
        self._user_text = _readable(text)
        self.user_label.setText(f"你：{self._user_text}")
        self.user_label.setVisible(True)
        self.user_block.setVisible(True)
        self._install_ask(self.user_label)
        if when:
            self.time_label.setText(when)

    def append_thinking(self, text: str) -> None:
        """追加思考过程的增量（与正文分开：思考要能折起来，不然答案被一大段推理推走）。"""
        if not text:
            return
        if not self._thinking_text:
            self.thinking.setVisible(True)          # 第一次来思考：露头
        self._thinking_text += text
        self.thinking_label.setText(self._thinking_text)
        self._sync_thinking()

    def toggle_thinking(self, *, open_: bool | None = None) -> None:
        """折叠 / 展开思考过程（点标题行，或代码里显式指定）。"""
        self._thinking_open = (not self._thinking_open) if open_ is None else bool(open_)
        self._sync_thinking()
        if self._thinking_open and self._thinking_text:
            # 展开后把标题行滚进视野：思考在小节上方，不滚的话用户以为"点了没反应"
            self.thinking_header.setFocus()

    def thinking_open(self) -> bool:
        return self._thinking_open

    def _sync_thinking(self) -> None:
        """标题行文字与正文可见性都由这里定（含实时字数：流式期间它是"还在想"的信号）。"""
        if not self._thinking_text:
            self.thinking.setVisible(False)
            return
        arrow = "▾" if self._thinking_open else "▸"
        size = len(self._thinking_text)
        tail = " · 思考中" if (self._thinking_open and not self._reply_text.strip()) else ""
        self.thinking_header.setText(f"{arrow} 思考过程（{size} 字{tail}）")
        self.thinking_label.setVisible(self._thinking_open)
        self.thinking.setVisible(True)

    def begin_reply(self, title: str = "模型回复") -> None:
        if self._thinking_text and self._thinking_open:
            # 开始写答案就自动收起思考：答案才是要读的东西，用户想看再点开
            self.toggle_thinking(open_=False)
        self.reply_header.setText(f"—— {title} ——")
        self.reply_header.setVisible(True)
        self.reply_body.setVisible(True)
        self.footer.setVisible(True)
        if self._stream_label is None:
            # 流式期间**按纯文本**显示：这一路可能是脚本契约（引擎那条流），
            # 边流边按 Markdown 解析会让 `# 注释` 在眼前变成巨型标题又缩回去。
            # 定稿时再按内容决定"Markdown 还是代码块"（见 finish_reply）。
            self._stream_label = _selectable_label("", "chatReplyText")
            self._install_ask(self._stream_label)
            self.reply_layout.addWidget(self._stream_label)

    def append_reply(self, text: str) -> None:
        """追加流式增量；没有正文块就先建一个（引擎可能不给标题直接开始吐字）。"""
        if not text:
            return
        if self._stream_label is None:
            self.begin_reply()
        # 判据是"正文里还没有**有内容**的字"：分节标记前后的换行会先落进正文里，
        # 用 `not self._reply_text` 会被那个换行骗过去（实测：思考一直不收）
        if self._thinking_text and not self._reply_text.strip() and self._thinking_open:
            # 正文**刚开始**就收起思考。不能只在 `begin_reply()` 里收：标题在流开始时就发过了，
            # 真正"开始写答案"是这里 —— 不收的话思考会一直占着屏幕（实测点开折叠才发现）。
            self.toggle_thinking(open_=False)
        assert self._stream_label is not None
        self._reply_text += text
        self._stream_label.setText(self._reply_text)

    def finish_reply(self) -> None:
        """流结束：把正文切成"散文 + 代码块"（代码块带复制按钮），并显示页脚。"""
        if self._segmented:
            return
        self._segmented = True
        if self._stream_label is not None:
            # 三步都要：出布局、**立刻从屏幕上拿掉**、再排删除。
            # 只 removeWidget + deleteLater 是不够的 —— deleteLater 要等事件循环，
            # 在这之前那个标签仍然是可见的散件，会按自己的旧几何画在卡片上，
            # 于是卡片里出现一层"重影文字"（实测抓图里看得很清楚，像排版重叠）。
            label = self._stream_label
            self.reply_layout.removeWidget(label)
            label.hide()
            label.setParent(None)
            label.deleteLater()
            self._stream_label = None
        if self._thinking_text:
            # 定稿：思考过程里也常有代码与 `#` 注释 —— 与正文同一套判据，
            # 像代码就按纯文本显示（否则同样会被 Markdown 变成巨型标题）
            self.thinking_label.setTextFormat(
                Qt.TextFormat.PlainText
                if looks_like_code(self._thinking_text)
                else Qt.TextFormat.MarkdownText
            )
            self.thinking_label.setText(self._thinking_text)
            self._sync_thinking()
        text = self._reply_text.strip("\n")
        if not text:
            return
        self.reply_body.setVisible(True)
        self.footer.setVisible(True)
        for kind, body, language in self._sections(text):
            if kind == "code":
                self.reply_layout.addWidget(self._code_block(body, language))
                continue
            label = _selectable_label(body, "chatReplyText", markdown=True)
            self._install_ask(label)
            self.reply_layout.addWidget(label)

    def _sections(self, text: str) -> list[tuple[str, str, str]]:
        """把一段模型输出拆成"该按代码显示"与"该按 Markdown 显示"的若干段。

        三层判据，从严到宽：
        1. **题面契约**（`===TU-SCRIPT===` …）：脚本进代码块，说明与假设进正文；
        2. **围栏代码块**：` ```bash ` 那套照旧单独成块；
        3. **看着像代码的散文段**：没有围栏、但整段就是脚本（模型常这么干）→ 也进代码块，
           否则 Markdown 会把 `#` 注释变成巨型标题、把代码行并成段落（用户报的那个截图）。
        """
        contract = split_contract(text)
        if contract:
            # 契约里的脚本没有围栏标记 → 语言留空（空 = 按 shell 上色，它本来就是 shell）
            return [(kind, body, "") for kind, body in contract]
        sections: list[tuple[str, str, str]] = []
        language = ""
        for kind, body in split_segments(text):
            if kind == "lang":
                language = body
                continue
            if kind == "code":
                sections.append(("code", body, language))
                language = ""
                continue
            sections.append(("code" if looks_like_code(body) else "prose", body, ""))
        return sections

    def add_activity(self, kind: str, text: str) -> None:
        """工具/阶段行（"读取 read_file（plan.md）"这类）：单独一行、次级色，像 DSH 的活动列表。"""
        row = QWidget()
        row.setObjectName("chatActivityRow")
        layout = QHBoxLayout(row)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        tag = QLabel(kind)
        tag.setObjectName("chatActivityTag")
        body = _selectable_label(text, "chatActivityText")
        body.setWordWrap(True)
        layout.addWidget(tag)
        layout.addWidget(body, 1)
        self.activity_layout.addWidget(row)
        self.activities.setVisible(True)

    def add_error(self, text: str) -> None:
        """错误块：**独立的块**，颜色与语义色一致 —— 夹在正文里会被当成模型说的话。"""
        block = QFrame()
        block.setObjectName("chatErrorBlock")
        layout = QVBoxLayout(block)
        layout.setContentsMargins(10, 8, 10, 8)
        label = _selectable_label(f"！：{text}", "chatErrorText")
        self._install_ask(label)
        layout.addWidget(label)
        self.reply_layout.addWidget(block)
        self.reply_body.setVisible(True)
        self.footer.setVisible(True)

    # ── 读出 ─────────────────────────────────────────────────────────────
    def has_content(self) -> bool:
        return self._has_user or bool(self._reply_text.strip()) or self.activity_layout.count() > 0

    def plain_text(self) -> str:
        """这一轮的纯文本（与旧版 `_append` 的拼法一致：`你：…` / `模型：…` / `·：…` / `！：…`）。"""
        parts: list[str] = []
        if self._has_user and self._user_text:
            parts.append(f"\n你：{self._user_text}\n")
        if self._thinking_text.strip():
            # 与旧版逐字一致：思考过程也在纯文本里（它以前是一段普通正文）。
            # 抠脚本取的是"最后一段围栏"，少一段会不会取错是另一回事 —— 先不做行为变更。
            parts.append(f"\n{THINKING_HEADER}\n{self._thinking_text}\n")
        if self._reply_text.strip():
            parts.append(f"\n模型：{self._reply_text}\n")
        for index in range(self.activity_layout.count()):
            row = self.activity_layout.itemAt(index).widget()
            if row is None:
                continue
            label = row.findChild(QLabel, "chatActivityText")
            if label is not None and label.text():
                parts.append(f"\n·：{label.text()}\n")
        for block in self.reply_body.findChildren(QFrame, "chatErrorBlock"):
            label = block.findChild(QLabel, "chatErrorText")
            if label is not None and label.text():
                parts.append(f"\n{label.text()}\n")
        return "".join(parts)

    # ── 内部 ─────────────────────────────────────────────────────────────
    def _code_block(self, code: str, language: str) -> QWidget:
        """一个代码块：块头（语言 + 复制）+ 等宽正文。"""
        block = QFrame()
        block.setObjectName("chatCodeBlock")
        layout = QVBoxLayout(block)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        head = QWidget()
        head.setObjectName("chatCodeHeader")
        head_layout = QHBoxLayout(head)
        head_layout.setContentsMargins(10, 4, 6, 4)
        head_layout.setSpacing(6)
        name = QLabel(language or "text")
        name.setObjectName("chatCodeLanguage")
        head_layout.addWidget(name)
        head_layout.addStretch(1)
        head_layout.addWidget(_CopyButton(lambda text=code: text, tip="复制这段代码"))
        layout.addWidget(head)
        # 代码正文用 CodeView（见它的文档：QLabel 断不开没有空格的长串，会被裁掉半个）
        body = CodeView(code, language)
        body.setContentsMargins(10, 6, 10, 8)
        layout.addWidget(body)
        return block

    def _install_ask(self, label: QLabel) -> None:
        if self._on_ask is None:
            return
        from .widgets.selection_menu import install_ask_action

        install_ask_action(label, self._on_ask, label="就选中的内容提问")


class TranscriptView(QScrollArea):
    """记录区：一串 `TurnView`，自动滚到底。

    **为什么是 `QScrollArea` 而不是 `QPlainTextEdit`**：卡片要圆角、要两栏对比（用户块 / 回复块）、
    要块内按钮 —— 纯文本控件做不到这些。纯文本那条契约由 `toPlainText()` 保留下来（见模块开头）。
    """

    def __init__(self, on_ask: Callable[[str], None] | None = None, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("chatTranscript")
        self.setWidgetResizable(True)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setMinimumHeight(64)
        # 视口自己别上色：不关掉的话它按调色板画一层 Base 色，把圆角与卡片底色盖掉
        # （QScrollArea 的视口是内部子控件，QSS 里点名它才行）
        self.viewport().setObjectName("chatTranscriptViewport")
        self.viewport().setAutoFillBackground(False)
        self._on_ask = on_ask
        body = QWidget()
        body.setObjectName("chatTranscriptBody")
        self._layout = QVBoxLayout(body)
        self._layout.setContentsMargins(8, 8, 8, 8)
        self._layout.setSpacing(10)
        # 空记录区的那句说明（`QScrollArea` 没有 placeholder，自己放一个标签）
        self._placeholder = QLabel("")
        self._placeholder.setObjectName("chatPlaceholder")
        self._placeholder.setWordWrap(True)
        self._placeholder.setAlignment(
            Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft
        )
        self._layout.addWidget(self._placeholder)
        self._layout.addStretch(1)
        self.setWidget(body)
        self._turns: list[TurnView] = []
        self._current: TurnView | None = None
        # 「跟着流走」：**只有用户本来就在底部**时才自动滚到底。
        # 用户实测（Windows）：流式期间想往上翻着看，每来一个字都被拽回底部 ——
        # 体感就是"滚动条拖不动，得等对话结束才能滑"。
        self._follow = True
        bar = self.verticalScrollBar()
        bar.rangeChanged.connect(self._on_range_changed)
        bar.valueChanged.connect(self._on_value_changed)
        bar.sliderPressed.connect(self._on_slider_pressed)
        bar.sliderReleased.connect(self._on_slider_released)

    # ── 自动滚动：跟着流走，但不跟用户抢 ──────────────────────────────────
    def is_following(self) -> bool:
        """当前是否处于"跟随流式输出"的状态（停在底部 = 跟随）。"""
        return self._follow

    def _at_bottom(self) -> bool:
        bar = self.verticalScrollBar()
        return bar.value() >= bar.maximum() - 4

    def _on_range_changed(self, _minimum: int, maximum: int) -> None:
        """内容变高时（布局完成才发这个信号）如果还在跟随，就贴到底。

        用 `rangeChanged` 而不是"追加时设一次最大值"：追加的那一刻布局还没完成、
        `maximum()` 还是旧值，直接设会**差一截**（实测：流式结束停在半路、根本没跟随）。
        """
        if self._follow:
            self.verticalScrollBar().setValue(maximum)

    def _on_value_changed(self, value: int) -> None:
        bar = self.verticalScrollBar()
        if bar.isSliderDown():
            return                      # 正在拖：位置由用户说了算，此刻不改跟随状态
        self._follow = value >= bar.maximum() - 4

    def _on_slider_pressed(self) -> None:
        self._follow = False            # 一上手拖就停止跟随，直到他自己回到最底下

    def _on_slider_released(self) -> None:
        self._follow = self._at_bottom()

    # ── 对外 ─────────────────────────────────────────────────────────────
    def setPlaceholderText(self, text: str) -> None:  # noqa: N802 - 与 QPlainTextEdit 同名
        self._placeholder.setText(text)
        self._placeholder.setVisible(not self._turns)

    def placeholderText(self) -> str:  # noqa: N802 - 同上
        return self._placeholder.text()

    def clear(self) -> None:
        for turn in self._turns:
            self._layout.removeWidget(turn)
            turn.setParent(None)
            turn.deleteLater()
        self._turns = []
        self._current = None
        self._placeholder.setVisible(True)

    def begin_turn(self) -> TurnView:
        """开一轮（用户发消息时调用）；上一轮就此定稿。"""
        turn = TurnView(on_ask=self._on_ask)
        self._layout.insertWidget(self._layout.count() - 1, turn)
        self._turns.append(turn)
        self._current = turn
        self._placeholder.setVisible(False)
        # 强制到底：刚开了新的一轮（用户发了消息 / 流式开始），他要看到这一轮的开头。
        # 这不是"抢滚动条"——它发生在新增内容的那一刻，而不是每次增量。
        self.scroll_to_bottom(force=True)
        return turn

    def current_turn(self) -> TurnView:
        """当前轮；还没有就新建一轮（模型先说话的情况：运行期间的流式输出）。"""
        if self._current is None:
            return self.begin_turn()
        return self._current

    def last_turn(self) -> TurnView | None:
        return self._turns[-1] if self._turns else None

    def plain_text(self) -> str:
        return "".join(turn.plain_text() for turn in self._turns)

    def toPlainText(self) -> str:  # noqa: N802 - 与 QPlainTextEdit 同名，调用方不用改
        return self.plain_text()

    def scroll_to_bottom(self, *, force: bool = False) -> None:
        """滚到底 —— **但用户正在往上看时不抢**。

        `force=True` 只给"程序主动跳转"用（例如载入历史记录后要停在最新一条）；
        流式追加一律走默认值：跟随状态由 `_follow` 决定（见上面的 `rangeChanged`）。
        """
        if force:
            # 强制到底 = "从现在起跟随"。这一步不能省：只 schedule 一次 `_do_scroll`，
            # 它可能跑在布局还没完成的时候（那时 `maximum()` 还很小，设了等于没设），
            # 而内容随后长高时**只有 `_follow` 为真**才会继续贴底 ——
            # "载入 40 轮历史后停在最顶上"就是这么来的（实测）。
            self._follow = True
        elif not self._follow:
            return
        # 延到事件循环下一拍：此刻控件还没完成布局，直接设最大值会差一截
        QTimer.singleShot(0, self._do_scroll)

    def _do_scroll(self) -> None:
        bar = self.verticalScrollBar()
        bar.setValue(bar.maximum())
