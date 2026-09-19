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

# 从回复里抠出代码块（```bash / ```sh / ``` 后面到下一个围栏）
_FENCE = re.compile(r"```[a-zA-Z0-9_+-]*\n(.*?)```", re.S)


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
        self.session_combo.setMinimumWidth(240)
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
        self.model_combo.setMinimumWidth(200)
        self.model_combo.addItem("", "")
        self.model_combo.lineEdit().setPlaceholderText("沿用会话模型")
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
        self.transcript.setMinimumHeight(120)
        self.transcript.setPlaceholderText(
            "这里显示你与模型的对话；运行期间的模型输出也会流式追加在这里。"
        )

        self.input = QPlainTextEdit()
        self.input.setObjectName("chatInput")
        self.input.setPlaceholderText(
            "问它：这条报告是什么意思 / 为什么第二轮失败了 / 把脚本改成先备份再删除…（Ctrl+Enter 发送）"
        )
        self.input.setFixedHeight(72)

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

        buttons = QHBoxLayout()
        buttons.addWidget(self.send_button)
        buttons.addWidget(self.cancel_button)
        buttons.addWidget(self.extract_button)
        buttons.addStretch(1)
        buttons.addWidget(QLabel("模型"))
        buttons.addWidget(self.model_combo)
        buttons.addWidget(self.model_button)

        layout = QVBoxLayout(self)
        layout.addWidget(session_row)
        layout.addWidget(QLabel("与模型对话（对话不会执行任何脚本）"))
        layout.addWidget(self.transcript, 1)
        layout.addWidget(self.input)
        layout.addLayout(buttons)
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
                self.session_combo.addItem("（未发现可恢复的会话）", "")
            index = self.session_combo.findData(current_run_dir) if current_run_dir else -1
            self.session_combo.setCurrentIndex(index if index >= 0 else 0)
        finally:
            self.session_combo.blockSignals(False)

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
        self.add_user(text)
        self.send_requested.emit(text)

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
