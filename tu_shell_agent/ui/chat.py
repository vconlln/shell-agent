"""模型对话面板：与当前 opencode 会话自由对话（规格之外的新增能力，2026-09-19）。

**定位（必须写在代码里，否则会被误用）**：这是一个"问它、让它解释"的通道，不是执行通道。
对话里模型写出的脚本**不会自动执行**：要执行得先点"把最新脚本放进中栏"，再走中栏那条
"改后重跑"（shellcheck → 人工确认 → 执行）。也就是说，对话绕过的只有"生成轮次"，
没有绕过任何一道闸门。

面板同时承担**流式输出**的展示：运行期间模型的增量文本（`assistant_delta` 事件）也往这个
记录区里追加。原因很实际 —— 生成一版脚本要几十秒，此前那几十秒界面上只有状态栏一句
"模型输出中…"，看不到模型在写什么；而"AI 在说话"这件事在界面里只该有一个去处。
"""

from __future__ import annotations

import re

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from .widgets.selection_menu import install_ask_action
from .widgets.wrap_row import WrapRow

# 从回复里抠出代码块（```bash / ```sh / ``` 后面到下一个围栏）
_FENCE = re.compile(r"```[a-zA-Z0-9_+-]*\n(.*?)```", re.S)

# 引用片段的上限：选中整份脚本（几百行）一次发给模型既贵又慢，模型注意力也会散。
# 超了就截断，并在交给模型的消息里**写明被截断**，不悄悄少发一段。
_QUOTE_MAX_LINES = 120
_QUOTE_MAX_CHARS = 6000


def _clamp_quote(text: str) -> tuple[str, bool]:
    """限长引用，返回 (正文, 是否截断过)。

    按行与字符两个上限取先到的那个：一行几万字符的压缩脚本也要挡（否则"行数没超"
    却照样把提示词撑爆）。
    """
    lines = text.rstrip("\n").split("\n")
    truncated = False
    if len(lines) > _QUOTE_MAX_LINES:
        lines = lines[:_QUOTE_MAX_LINES]
        truncated = True
    body = "\n".join(lines)
    if len(body) > _QUOTE_MAX_CHARS:
        body = body[:_QUOTE_MAX_CHARS].rstrip()
        truncated = True
    return body, truncated


def extract_last_script(text: str) -> str | None:
    """取回复里**最后一段**围栏代码块；没有就返回 None。

    取最后一段而不是第一段：模型常见写法是"先展示现状、再给出改好的版本"，
    用户想要的是后者。
    """
    blocks = [match.group(1) for match in _FENCE.finditer(text)]
    if not blocks:
        return None
    script = blocks[-1].strip("\n")
    return script if script.strip() else None


class ChatPanel(QWidget):
    """对话记录 + 输入框 + 发送/取消/把脚本送进中栏。"""

    send_requested = Signal(str)      # 用户点了发送（携带输入内容）
    cancel_requested = Signal()
    script_extracted = Signal(str)    # "把最新脚本放进中栏"（携带脚本正文）
    session_selected = Signal(str)    # 会话下拉选中（携带运行目录）
    sessions_refresh_requested = Signal()
    new_session_requested = Signal()
    proposal_accepted = Signal()       # 用户接受模型提议的脚本
    proposal_accepted_run = Signal()   # 接受并立刻重跑（校验 + 执行，仍走人工确认闸门）
    proposal_rejected = Signal()       # 用户拒绝（中栏脚本保持不动）
    model_changed = Signal(str)        # 对话用的模型改了（provider/model，空 = 用会话默认）
    models_requested = Signal()        # 需要可用模型列表（首次显示 / 点刷新）

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("chatPanel")

        # ── 会话条：从**文件夹**里扫出来的历史会话 ──────────────────────
        # 会话不再只在内存里：每个运行目录都记着它的 sessionId，这里直接列出来，
        # 选中就接着那一段聊（记录也会从目录里的 chat.jsonl 回填）。
        self._models_requested_once = False
        self.session_combo = QComboBox()
        self.session_combo.setObjectName("chatSessionCombo")
        # 240 会把整栏的**最小宽度**顶到 599px（控制台里它是矮面板时无所谓，搬进右列之后
        # 就成了问题：1440 宽的窗口里右列吃 599，左栏被挤到 284）。下拉本身可以缩，
        # 缩窄时显示走省略号，比"挤扁左栏"好。
        self.session_combo.setMinimumWidth(150)
        self.session_combo.currentIndexChanged.connect(self._on_session_changed)
        self.refresh_button = QPushButton("扫描历史会话")
        self.refresh_button.setObjectName("chatSessionRefreshButton")
        self.refresh_button.clicked.connect(lambda: self.sessions_refresh_requested.emit())
        self.new_button = QPushButton("新对话")
        self.new_button.setObjectName("chatSessionNewButton")
        self.new_button.clicked.connect(lambda: self.new_session_requested.emit())

        # 模型：只影响**这段对话**（opencode 的 message 接口支持逐条指定模型，
        # 所以换模型不必重建会话、也不动 agent 文件）。
        self.model_combo = QComboBox()
        self.model_combo.setObjectName("chatModelCombo")
        self.model_combo.setEditable(True)
        self.model_combo.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        self.model_combo.setMinimumWidth(110)   # 同上：给右列留出可缩的余地
        self.model_combo.addItem("", "")
        self.model_combo.lineEdit().setPlaceholderText("使用会话模型")
        self.model_combo.setToolTip(
            "只影响这段对话（按条指定给 opencode）；生成脚本用的是「设置 → 模型」里的那个。"
        )
        self.model_combo.currentTextChanged.connect(self._on_model_changed)
        self.model_button = QPushButton("可用模型")
        self.model_button.setObjectName("chatModelRefreshButton")
        self.model_button.clicked.connect(lambda: self.models_requested.emit())

        session_row = QWidget()
        session_row.setObjectName("chatSessionRow")
        session_layout = QHBoxLayout(session_row)
        session_layout.setContentsMargins(0, 0, 0, 0)
        session_layout.addWidget(QLabel("会话"))
        session_layout.addWidget(self.session_combo, 1)
        session_layout.addWidget(self.refresh_button)
        session_layout.addWidget(self.new_button)
        # 模型控件放**底部按钮行**的右端：与「发送 / 取消 / 把最新脚本放进中栏」同一行。
        # 会话那行只留会话本身，上面不再堆两排控件。

        self.transcript = QPlainTextEdit()
        self.transcript.setObjectName("chatTranscript")
        self.transcript.setReadOnly(True)
        # 120 太高了：Windows 150% 缩放下工具区常常只有两三百逻辑像素，
        # 记录 120 + 输入 72 就把面板顶到 324，窗口地板被抬到 666 —— 再没余量就重叠。
        self.transcript.setMinimumHeight(64)
        self.transcript.setPlaceholderText(
            "显示与模型的对话；运行期间的模型输出也会追加在此处。"
        )
        # 记录区里选中的内容也能提问（想追问模型上一句里的某段代码时最常用）
        install_ask_action(
            self.transcript,
            lambda text: self._quote(text, "对话记录"),
            label="就选中的内容提问",
        )

        self.input = QPlainTextEdit()
        self.input.setObjectName("chatInput")
        self.input.setPlaceholderText(
            "输入问题，例如：该报告的含义 / 第二轮失败的原因 / 将脚本改为先备份再删除。（Ctrl+Enter 发送）"
        )
        self.input.setFixedHeight(56)

        self.send_button = QPushButton("发送")
        self.send_button.setObjectName("chatSendButton")
        self.cancel_button = QPushButton("取消")
        self.cancel_button.setObjectName("chatCancelButton")
        self.extract_button = QPushButton("把最新脚本放进中栏")
        self.extract_button.setObjectName("chatExtractButton")
        self.status = QLabel("尚未建立对话会话；点「发送」会自动建立一个。")
        self.status.setObjectName("chatStatus")
        self.status.setProperty("role", "muted")
        self.status.setWordWrap(True)

        # 底部这行：左边是动作（发送/取消/把最新脚本放进中栏），右边是模型选择。
        # 用 WrapRow 而不是 QHBoxLayout：右列被压窄时它**折行**而不是让控件重叠
        # （实测窗口 960 宽时右列 386px，模型下拉会盖住「可用模型」按钮）。
        self.model_label = QLabel("模型")
        self.controls_row = WrapRow(
            [self.send_button, self.cancel_button, self.extract_button,
             self.model_label, self.model_combo, self.model_button],
            gap=3,
        )

        # ── 模型提议栏：像 Cursor 那样给出"接受 / 拒绝" ──────────────────
        # 差异正文显示在**中栏**（那里有地方、也已有一套红绿 diff 渲染），
        # 这里只放一行摘要与两个按钮 —— 工具区本来就矮，不该再塞一个大 diff 框。
        self.proposal_label = QLabel()
        self.proposal_label.setObjectName("proposalLabel")
        self.proposal_label.setWordWrap(True)
        self.accept_button = QPushButton("接受")
        self.accept_button.setObjectName("proposalAcceptButton")
        self.reject_button = QPushButton("拒绝")
        self.reject_button.setObjectName("proposalRejectButton")
        # 「接受并重跑」：接受之后的下一步永远是"改后重跑"，合成一次点击
        # （走后同一条闸门：shellcheck → 人工确认 → 执行，不跳过任何一道）。
        self.accept_run_button = QPushButton("接受并重跑")
        self.accept_run_button.setObjectName("proposalAcceptRunButton")
        self.accept_run_button.clicked.connect(lambda: self.proposal_accepted_run.emit())
        self.accept_button.clicked.connect(lambda: self.proposal_accepted.emit())
        self.reject_button.clicked.connect(lambda: self.proposal_rejected.emit())
        self.proposal_bar = QWidget()
        self.proposal_bar.setObjectName("proposalBar")
        proposal_layout = QHBoxLayout(self.proposal_bar)
        proposal_layout.setContentsMargins(0, 0, 0, 0)
        proposal_layout.addWidget(self.proposal_label, 1)
        proposal_layout.addWidget(self.accept_button)
        proposal_layout.addWidget(self.accept_run_button)
        proposal_layout.addWidget(self.reject_button)
        self.proposal_bar.setVisible(False)
        # 状态位单独记：`isVisible()` 在父级（工具区页签）没被选中时永远是 False，
        # 用它当"有没有提议"会把"在别的页签里等着"误判成"没有提议"（用例里踩到过）。
        self._has_proposal = False

        # ── 引用条：从中栏选中的代码 / 记录里选中的片段 ──────────────────
        # 用户要求"对话也可以选中代码进行对话，询问代码"。引用内容会**原样**出现在
        # 发出的消息里（记录里也看得到），不做隐藏的提示词注入：用户能看到自己发了什么。
        self.quote_label = QLabel()
        self.quote_label.setObjectName("chatQuoteLabel")
        self.quote_label.setProperty("role", "hint")
        self.quote_clear_button = QPushButton("取消引用")
        self.quote_clear_button.setObjectName("chatQuoteClearButton")
        self.quote_clear_button.clicked.connect(self.clear_quote)
        self.quote_bar = QWidget()
        self.quote_bar.setObjectName("chatQuoteBar")
        quote_layout = QHBoxLayout(self.quote_bar)
        quote_layout.setContentsMargins(8, 4, 8, 4)
        quote_layout.addWidget(self.quote_label, 1)
        quote_layout.addWidget(self.quote_clear_button)
        self.quote_bar.setVisible(False)
        self._quote_text = ""
        self._quote_source = ""
        self._quote_truncated = False

        layout = QVBoxLayout(self)
        layout.addWidget(session_row)
        layout.addWidget(self.proposal_bar)
        layout.addWidget(QLabel("与模型对话（对话不会执行任何脚本）"))
        layout.addWidget(self.transcript, 1)
        layout.addWidget(self.quote_bar)
        layout.addWidget(self.input)
        layout.addWidget(self.controls_row)
        layout.addWidget(self.status)

        self.send_button.clicked.connect(self._on_send)
        self.cancel_button.clicked.connect(lambda _checked=False: self.cancel_requested.emit())
        self.extract_button.clicked.connect(self._on_extract)
        self.set_busy(False)

    # ── 对外 ──────────────────────────────────────────────────────────────
    # ── 会话 ──────────────────────────────────────────────────────────────
    def set_sessions(self, sessions, current_run_dir: str = "") -> None:
        """铺会话列表；`current_run_dir` 是当前正在用的那段，选中它但不触发切换。"""
        self.session_combo.blockSignals(True)
        try:
            self.session_combo.clear()
            for item in sessions:
                self.session_combo.addItem(item.label(), item.run_dir)
            if not sessions:
                self.session_combo.addItem("未发现可恢复的会话", "")
            index = self.session_combo.findData(current_run_dir) if current_run_dir else -1
            self.session_combo.setCurrentIndex(index if index >= 0 else 0)
        finally:
            self.session_combo.blockSignals(False)

    def show_proposal(self, summary: str) -> None:
        """显示"模型提出了修改"这一行（差异正文在中栏），带接受/拒绝。"""
        self.proposal_label.setText(summary)
        self.proposal_bar.setVisible(True)
        self._has_proposal = True

    def clear_proposal(self) -> None:
        self.proposal_bar.setVisible(False)
        # 状态位单独记：`isVisible()` 在父级（工具区页签）没被选中时永远是 False，
        # 用它当"有没有提议"会把"在别的页签里等着"误判成"没有提议"（用例里踩到过）。
        self._has_proposal = False
        self.proposal_label.clear()

    def has_proposal(self) -> bool:
        """有没有待决定的提议（与"当前是否可见"无关：它可能正在别的页签里等着）。"""
        return self._has_proposal

    # ── 引用（选中代码/记录提问）──────────────────────────────────────────
    def set_quote(self, text: str, source: str = "") -> None:
        """放进一段引用：显示引用条，并把它作为下一条消息的上下文。

        截断在这里做，且**说明**清楚：消息体里会写"（引用已截断…）"，
        用户在记录里能看到自己实际问了什么。
        """
        if not text.strip():
            self.clear_quote()
            return
        self._quote_text, truncated = _clamp_quote(text)
        self._quote_source = (source or "").strip()
        self._quote_truncated = truncated
        lines = self._quote_text.count("\n") + 1
        head = f"已引用 {self._quote_source}" if self._quote_source else "已引用选中内容"
        tail = f"（{lines} 行，超出部分已截断）" if truncated else f"（{lines} 行）"
        self.quote_label.setText(f"{head}{tail}")
        self.quote_bar.setVisible(True)

    def clear_quote(self) -> None:
        self._quote_text = ""
        self._quote_source = ""
        self._quote_truncated = False
        self.quote_label.clear()
        self.quote_bar.setVisible(False)

    def quote(self) -> tuple[str, str]:
        """当前引用的 (正文, 来源)；没有引用时是 ("", "")。"""
        return self._quote_text, self._quote_source

    def has_quote(self) -> bool:
        """有没有引用（与"是否可见"无关，理由同 has_proposal）。"""
        return bool(self._quote_text.strip())

    def _quote(self, text: str, source: str) -> None:
        """右键菜单进来的入口：引用后把焦点交回输入框，用户可以接着打字。"""
        self.set_quote(text, source)
        self.input.setFocus()

    def compose_message(self, question: str) -> str:
        """把问题与引用拼成实际发给模型的那一条消息。

        引用用 `~~~` 围栏而不是 ``` ：脚本/报告里本来就可能出现三个反引号，
        用反引号围栏会被内容提前闭合（这类"围栏被内容截断"的问题在 diff 里也踩过）。
        """
        text, source = self.quote()
        if not text:
            return question
        head = f"关于以下引用内容（{source}）：" if source else "关于以下引用内容："
        note = "（引用已截断，只发送了开头部分）" if self._quote_truncated else ""
        return f"{head}{note}\n\n~~~\n{text}\n~~~\n\n{question}"

    def set_models(self, models) -> None:
        """铺可用模型列表；**保留当前选择**（刷新不该把已选的模型弄丢）。"""
        current = self.model_combo.currentText().strip()
        self.model_combo.blockSignals(True)
        try:
            self.model_combo.clear()
            self.model_combo.addItem("", "")
            for model in models:
                self.model_combo.addItem(str(model), str(model))
            index = self.model_combo.findText(current)
            if index >= 0:
                self.model_combo.setCurrentIndex(index)
            else:
                self.model_combo.setEditText(current)
        finally:
            self.model_combo.blockSignals(False)

    def set_model(self, model: str) -> None:
        """外部（恢复会话/新建会话）设定当前模型，不触发 model_changed。"""
        text = (model or "").strip()
        self.model_combo.blockSignals(True)
        try:
            index = self.model_combo.findText(text)
            if index >= 0:
                self.model_combo.setCurrentIndex(index)
            else:
                self.model_combo.setEditText(text)
        finally:
            self.model_combo.blockSignals(False)

    def selected_model(self) -> str:
        return self.model_combo.currentText().strip()

    def _on_model_changed(self, text: str) -> None:
        self.model_changed.emit(text.strip())

    def showEvent(self, event) -> None:  # noqa: N802 - Qt 命名
        """第一次显示时才去问可用模型：跑一次 `opencode models` 是子进程，
        没必要在启动路径上付这个代价（设置页的字体列表同理）。"""
        super().showEvent(event)
        if not self._models_requested_once:
            self._models_requested_once = True
            self.models_requested.emit()

    def selected_session(self) -> str:
        return str(self.session_combo.currentData() or "")

    def _on_session_changed(self, _index: int) -> None:
        run_dir = self.selected_session()
        if run_dir:
            self.session_selected.emit(run_dir)

    def clear_history(self) -> None:
        self.transcript.clear()

    def load_history(self, entries) -> None:
        """把磁盘上的对话记录回填进面板（角色 → 说话人，与实时追加同一套呈现）。"""
        self.clear_history()
        from ..run_store.sessions import ROLE_LABELS

        for entry in entries:
            speaker = ROLE_LABELS.get(entry.role, entry.role)
            stamp = f"（{entry.when}）" if entry.when else ""
            self._append_raw(f"\n{speaker}{stamp}：{entry.text}\n")
        self.transcript.ensureCursorVisible()

    def set_busy(self, busy: bool) -> None:
        self.send_button.setEnabled(not busy)
        self.cancel_button.setEnabled(busy)
        self.input.setReadOnly(busy)

    def add_user(self, text: str) -> None:
        self._append("你", text)

    def add_assistant(self, text: str) -> None:
        self._append("模型", text)

    def add_note(self, text: str) -> None:
        """系统提示（阶段、错误、取消）——与对话正文区分开，避免读成模型说的话。"""
        self._append("·", text)

    def begin_stream(self, title: str) -> None:
        """开始一段流式输出：先写标题行，后续 append_delta 往同一段里追加。"""
        self._append_raw(f"\n── {title} ──\n")

    def append_delta(self, text: str) -> None:
        self._append_raw(text)

    def add_error(self, text: str) -> None:
        self._append("！", text)

    def set_status(self, text: str) -> None:
        self.status.setText(text)

    def transcript_text(self) -> str:
        return self.transcript.toPlainText()

    # ── 内部 ──────────────────────────────────────────────────────────────
    def _append(self, speaker: str, text: str) -> None:
        self._append_raw(f"\n{speaker}：{text}\n")

    def _append_raw(self, text: str) -> None:
        cursor = self.transcript.textCursor()
        cursor.movePosition(cursor.MoveOperation.End)
        cursor.insertText(text)
        self.transcript.setTextCursor(cursor)
        self.transcript.ensureCursorVisible()

    def _on_send(self) -> None:
        text = self.input.toPlainText().strip()
        if not text:
            return
        self.input.clear()
        # 引用是**这一条**消息的上下文：发出去就撤掉，免得下一条问题又莫名其妙带上它
        message = self.compose_message(text)
        self.add_user(message)
        self.clear_quote()
        self.send_requested.emit(message)

    def _on_extract(self) -> None:
        script = extract_last_script(self.transcript_text())
        if script is None:
            self.add_note("回复里没有找到代码块，没有可提取的脚本。")
            return
        self.script_extracted.emit(script)

    def keyPressEvent(self, event) -> None:  # noqa: N802 - Qt 命名
        # Ctrl+Enter 发送（聊天框的常规习惯）；单独 Enter 留给换行
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter) and (
            event.modifiers() & Qt.KeyboardModifier.ControlModifier
        ):
            self._on_send()
            return
        super().keyPressEvent(event)
