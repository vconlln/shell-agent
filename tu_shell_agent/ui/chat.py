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

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("chatPanel")

        self.transcript = QPlainTextEdit()
        self.transcript.setObjectName("chatTranscript")
        self.transcript.setReadOnly(True)
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

        layout = QVBoxLayout(self)
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
