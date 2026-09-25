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
import time

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QAction
from PySide6.QtWidgets import (
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMenu,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ..agent_backends.api_client import ANSWER_HEADER, THINKING_HEADER
from .chat_view import TranscriptView
from .widgets.wrap_row import WrapRow

# 从回复里抠出代码块（```bash / ```sh / ``` 后面到下一个围栏）
_FENCE = re.compile(r"```[a-zA-Z0-9_+-]*\n(.*?)```", re.S)

# "重新获取可用模型"那一项的哨兵值：放在 itemData 里，不会被当成模型名发出去
REFRESH_MODELS = object()

# 点开下拉时，多久算"列表还新鲜"（秒）。在这之内不再去要，避免连点几下就起好几个子进程；
# 想强制刷新有末项「重新获取可用模型」。
_MODEL_LIST_TTL_S = 300.0


def _activity_tag(text: str) -> str:
    """活动行前面那个小标签：按内容分档，认不出来就是"提示"。"""
    body = (text or "").strip()
    if body.startswith("读取 "):
        return "读取"
    if "技能" in body:
        return "技能"
    if body.startswith("工具调用"):
        return "工具"
    return "提示"


class ChatInput(QPlainTextEdit):
    """输入框：**Enter 发送、Ctrl+Enter 换行**（用户 2026-09-20 要求）。

    改之前是反的（Enter 换行、Ctrl+Enter 发送）：在聊天框里敲完一句话按回车是所有人都有的
    肌肉记忆，而"想换行按 Ctrl"与记事本/浏览器地址栏的习惯也一致。`Shift+Enter` 同样换行
    （老习惯，一起留着不冲突）。

    为什么要子类：`QPlainTextEdit` 自己会吃掉回车（插换行），父控件的 `keyPressEvent` 拿不到，
    所以只能在输入框这一层拦。
    """

    send_requested = Signal()

    def keyPressEvent(self, event) -> None:  # noqa: N802 - Qt 命名
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            modifiers = event.modifiers()
            if modifiers & (
                Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.ShiftModifier
            ):
                self.insertPlainText("\n")      # 明确插一行：不指望基类在这个修饰键下的行为
                return
            self.send_requested.emit()
            return
        super().keyPressEvent(event)


class ModelCombo(QComboBox):
    """模型下拉：**点开时先要一次"可用模型"，再往上弹列表**。

    为什么要子类：`showPopup()` 是虚函数，只有子类才拦得到"用户点开这个下拉"这个动作 ——
    点开是"我现在要挑一个模型"的明确信号，这时候去取列表最合适（取列表在部分后端上是起子进程，
    不能每次刷新界面都跑一遍）。列表现取现用，比"先点「可用模型」按钮、再点下拉"少一步。
    """

    popup_about_to_open = Signal()

    def showPopup(self) -> None:  # noqa: N802 - Qt 命名
        self.popup_about_to_open.emit()
        super().showPopup()


# 引用片段的上限：选中整份脚本（几百行）一次发给模型既贵又慢，模型注意力也会散。
# 超了就截断，并在交给模型的消息里**写明被截断**，不悄悄少发一段。
_QUOTE_MAX_LINES = 120
_QUOTE_MAX_CHARS = 6000


def _clamp_quote(text: str) -> tuple[str, bool]:
    """规范化并限长引用，返回 (正文, 是否截断过)。

    先统一换行：Windows 上来的文本可能是 CRLF（模型输出、脚本文件都可能是），
    而 `\r` 进了提示词与围栏判定只会变脏 —— Qt 控件那条路已经换成 `\n` 了，
    但 `set_quote()` 是公开入口，别的调用方不一定经过控件。

    限长按行与字符两个上限取先到的那个：一行几万字符的压缩脚本也要挡
    （否则"行数没超"却照样把提示词撑爆）。
    """
    text = text.replace("\r\n", "\n").replace("\r", "\n")
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
    backend_changed = Signal(str)      # 模式胶囊切换了后端 agent（携带后端 id）
    console_requested = Signal()       # 想打开「控制台 → 设置」（胶囊菜单里的最后一手）

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
        # 可用模型列表就在这个下拉里 —— 点开时现取现列，
        # 末项固定是「重新获取可用模型」，所以不再单独占一个按钮的位置：
        # 底栏那一行要塞下发送/取消/存入中栏 + 模型，多一个按钮就会挤到第二行
        # （用户实测："把这个模型移动到跟发送在同一行"）。
        self.model_combo = ModelCombo()
        self.model_combo.setObjectName("chatModelCombo")
        self.model_combo.setEditable(True)      # 完整模型名仍可直接输入（CLI 后端常用）
        self.model_combo.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        self.model_combo.setMinimumWidth(130)
        self.model_combo.addItem("", "")
        # 占位文字取短的：右列 150% 下只有 400 逻辑像素，"使用会话模型" 会被截成
        # "使用会…"；含义不变（留空 = 用会话/设置里那个模型），细节在提示气泡里
        self.model_combo.lineEdit().setPlaceholderText("会话模型")
        self.model_combo.setToolTip(
            "对话用的模型：点开可选择可用模型（末项「重新获取可用模型」会重新向后台要一次列表），"
            "也可以直接输入完整模型名；留空则用会话默认。\n"
            "只影响这段对话；生成脚本用的是「设置 → 模型」里的那个。"
        )
        self.model_combo.currentTextChanged.connect(self._on_model_changed)
        self.model_combo.popup_about_to_open.connect(self._on_model_popup_opened)
        self._models_loaded_at: float | None = None

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

        self.transcript = TranscriptView(on_ask=lambda text: self._quote(text, "对话记录"))
        # 记录区不再是一个纯文本控件，而是"每轮一张卡片"的滚动区（见 chat_view.py）：
        # 用户块与回复块各自成块、卡内带活动行与时间/复制 —— 旧版把所有轮次拼成一片文字，
        # 用户的原话是"每一轮会话都在一块，看不清楚"。
        self.transcript.setPlaceholderText(
            "显示与模型的对话；运行期间的模型输出也会追加在此处。"
        )

        self.input = ChatInput()
        self.input.setObjectName("chatInput")
        self.input.setPlaceholderText(
            "发消息或做任务…（Enter 发送 / Ctrl+Enter 换行）"
        )
        self.input.setFixedHeight(56)
        self.input.send_requested.connect(self._on_send)

        # 发送：圆形主色按钮（↑）—— 与"发送"两个字相比，圆形更像聊天工具，
        # 也把底部那一行让给模型与模式选择（用户给的参照图就是这个形状）。
        self.send_button = QPushButton("↑")
        self.send_button.setObjectName("chatSendButton")
        self.send_button.setProperty("role", "primary")
        self.send_button.setToolTip("发送（Enter）")
        # 取消：圆形停止按钮，**只在忙时出现**（不忙时它没有对象，占着位置只会挤）
        self.cancel_button = QPushButton("■")
        self.cancel_button.setObjectName("chatCancelButton")
        self.cancel_button.setToolTip("取消本轮生成")
        self.cancel_button.setVisible(False)

        # 「＋」：次要动作收进菜单（把最新脚本放进中栏 / 扫描历史会话 / 新对话）。
        # 三个都是"偶尔用一次"的动作，各自占一个按钮会把底部那一行挤爆（右列最小 385px）。
        self.plus_button = QPushButton("＋")
        self.plus_button.setObjectName("chatPlusButton")
        self.plus_button.setToolTip("更多：把最新脚本放进中栏 / 扫描历史会话 / 新对话")
        self.extract_action = QAction("把最新脚本放进中栏", self)
        self.extract_action.setToolTip("把回复里最新的一段脚本放进中栏（之后可「改后重跑」）")
        self.extract_action.triggered.connect(lambda _checked=False: self._on_extract())
        self.plus_menu = QMenu(self.plus_button)
        self.plus_menu.addAction(self.extract_action)
        self.plus_menu.addSeparator()
        self.plus_menu.addAction("扫描历史会话", lambda: self.sessions_refresh_requested.emit())
        self.plus_menu.addAction("新对话", lambda: self.new_session_requested.emit())
        self.plus_button.setMenu(self.plus_menu)

        # 模式胶囊：当前**后端 agent**（内置 agent / opencode / claude / codeagent…）。
        # 参照图里那个胶囊是"权限模式"，这一路没有那个概念 —— 真正决定"谁在回答"的是后端，
        # 所以胶囊就是它：点开即切，切完这句话就用新后端（与设置页里改「后端」同一件事）。
        self.mode_button = QPushButton(self._backend_label())
        self.mode_button.setObjectName("chatModeButton")
        self.mode_button.setToolTip("当前回答你的是哪个 agent 后端；点开可切换（与设置页里的「后端」同一项）")
        self.mode_menu = QMenu(self.mode_button)
        self._fill_mode_menu()
        self.mode_button.setMenu(self.mode_menu)

        # 兼容旧行为：面板（而不是输入框）拿到 Ctrl+Enter 时仍然发送
        self.status = QLabel("尚未建立对话会话；点「发送」会自动建立一个。")
        self.status.setObjectName("chatStatus")
        self.status.setProperty("role", "muted")
        self.status.setWordWrap(True)

        # 底部这一行：左侧「＋ + 模式胶囊」、右侧「模型 + 取消 + 发送」。
        # 仍然用 WrapRow：兜底 —— 窗口被压到比最小尺寸还小时**折行**而不是让控件重叠
        # （实测早期版本在右列 386px 时模型下拉盖住旁边的按钮）。
        self.controls_row = WrapRow(
            [self.plus_button, self.mode_button, self.model_combo, self.cancel_button, self.send_button],
            gap=2,      # 左组＝入口（＋ / 模式），右组＝模型与发送（宽的时候贴右端）
        )
        # 输入框与这一行同属一个"输入卡片"（参照图的形状：输入区有圆角外框，按钮在框内下沿）
        self.composer = QFrame()
        self.composer.setObjectName("chatComposer")
        composer_layout = QVBoxLayout(self.composer)
        composer_layout.setContentsMargins(8, 6, 8, 6)
        composer_layout.setSpacing(4)
        composer_layout.addWidget(self.input)
        composer_layout.addWidget(self.controls_row)

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
        # 上一次**真正**选中的模型文本：选中「重新获取可用模型」时用它还原选择
        self._model_text = ""
        # 当前流式片段属于思考还是正文（由 append_delta 里的分节标记切换）
        self._stream_section = "reply"

        layout = QVBoxLayout(self)
        layout.addWidget(session_row)
        layout.addWidget(self.proposal_bar)
        layout.addWidget(QLabel("与模型对话（对话不会执行任何脚本）"))
        layout.addWidget(self.transcript, 1)
        layout.addWidget(self.quote_bar)
        layout.addWidget(self.composer)
        layout.addWidget(self.status)

        self.send_button.clicked.connect(self._on_send)
        self.cancel_button.clicked.connect(lambda _checked=False: self.cancel_requested.emit())
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

    # ── 模型（可用模型列表就在这个下拉里）────────────────────────────────
    def set_models(self, models) -> None:
        """铺可用模型列表；**保留当前选择**（刷新不该把已选的模型弄丢）。

        末项固定是「重新获取可用模型」（数据是哨兵 `REFRESH_MODELS`，不会被当成模型名）。
        """
        current = self.model_combo.currentText().strip()
        self.model_combo.blockSignals(True)
        try:
            self.model_combo.clear()
            self.model_combo.addItem("", "")                    # 空 = 用会话模型
            known = False
            for model in models:
                name = str(model)
                self.model_combo.addItem(name, name)
                known = known or name == current
            self.model_combo.addItem("重新获取可用模型", REFRESH_MODELS)
            if known:
                self.model_combo.setCurrentIndex(self.model_combo.findText(current))
            else:
                self.model_combo.setEditText(current)
        finally:
            self.model_combo.blockSignals(False)
        # 弹层要比下拉本身宽：`deepseek/deepseek-v4-flash` 这种名字在 130px 的下拉里会被截断，
        # 而"选哪个模型"恰恰要看清全名。Qt 的弹层宽度默认跟控件一样，得自己撑开。
        self.model_combo.view().setMinimumWidth(self._model_list_width())
        self._model_text = current
        # 记下"列表是什么时候取到的"：点开下拉时用 TTL 判断还要不要再取一次
        self._models_loaded_at = time.monotonic()

    def _model_list_width(self) -> int:
        """弹层宽度：放得下最长的那个模型名（不超过屏幕的九成、也不超过 520px）。"""
        from PySide6.QtGui import QFontMetrics

        metrics = QFontMetrics(self.model_combo.view().font())
        widest = max(
            (metrics.horizontalAdvance(self.model_combo.itemText(index))
             for index in range(self.model_combo.count())),
            default=0,
        )
        want = widest + 48                      # 左右内边距 + 可能的滚动条
        screen = self.screen()
        limit = int(screen.availableGeometry().width() * 0.9) if screen is not None else 520
        return max(self.model_combo.minimumWidth(), min(want, 520, limit))

    def _on_model_popup_opened(self) -> None:
        """点开下拉＝"我要挑模型"：列表还没取过（或放了太久）就去要一次。

        为什么要 TTL 而不是每次都取：取列表在部分后端上是起子进程（`opencode models`），
        用户连点几下下拉就会同时跑好几个 —— 而且列表内容是异步回来的，正在打开的弹层被
        清空重填在平台上还可能自己关掉。想强制刷新有末项「重新获取可用模型」。
        """
        now = time.monotonic()
        if self._models_loaded_at is not None and now - self._models_loaded_at < _MODEL_LIST_TTL_S:
            return
        self.models_requested.emit()

    def _refresh_models_because_item_chosen(self) -> None:
        """选中了「重新获取可用模型」：它不是模型名，重新取列表并把选择还原。"""
        previous = self._model_text
        self.model_combo.blockSignals(True)
        try:
            self.model_combo.setEditText(previous)
        finally:
            self.model_combo.blockSignals(False)
        self.models_requested.emit()

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
        # 记住它：这是"当前真正选中的模型"，选中「重新获取可用模型」时要还原回这个值
        self._model_text = text

    def selected_model(self) -> str:
        return self.model_combo.currentText().strip()

    def _on_model_changed(self, text: str) -> None:
        if self.model_combo.currentData() is REFRESH_MODELS:
            # 「重新获取可用模型」被选中：它不是一个模型名，绝不能当成模型发出去
            self._refresh_models_because_item_chosen()
            return
        self._model_text = text.strip()
        self.model_changed.emit(self._model_text)

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
        """把磁盘上的对话记录回填进面板：**每条回复一轮**，用户消息与模型回复分别成块。

        旧版是按"角色 → 说话人"拼成一片文字（`你：…` / `模型：…`），回填出来同样看不出轮次；
        现在按顺序把 user 开一轮、其余（模型回复 / 错误）挂进这一轮里，与实时对话一模一样。
        """
        from ..run_store.sessions import ROLE_LABELS

        self.clear_history()
        for entry in entries:
            role = str(getattr(entry, "role", "") or "")
            text = str(getattr(entry, "text", "") or "")
            stamp = str(getattr(entry, "when", "") or "")
            if role == "user":
                turn = self.transcript.begin_turn()
                turn.set_user(text, when=stamp[-5:] if stamp else "")
            elif role in ("error", "failed"):
                self.transcript.current_turn().add_error(text)
            else:
                turn = self.transcript.current_turn()
                turn.begin_reply(ROLE_LABELS.get(role, "模型回复"))
                turn.append_reply(text)
                turn.finish_reply()
        self.transcript.scroll_to_bottom()

    def set_busy(self, busy: bool) -> None:
        # 发送按钮"能用 == 空闲"这一条**保留**：它是外部（用例、控制器）判断"这一轮结束了"
        # 的可观察标志。刻意**不**按"输入框有没有字"来禁用 —— 那样空闲但没打字时按钮也是灰的，
        # 这个标志就失效了（改这一版时踩到过：五条既有用例等的就是这个信号）。
        self.send_button.setEnabled(not busy)
        self.cancel_button.setEnabled(busy)
        # 停止按钮只在忙时出现：不忙时它没有对象，占着位置只会把那一行挤窄
        self.cancel_button.setVisible(busy)
        self.input.setReadOnly(busy)

    def add_user(self, text: str) -> None:
        """开一轮新对话（用户发了一条消息）——上一轮就此定稿。"""
        previous = self.transcript.last_turn()
        if previous is not None:
            previous.finish_reply()
        self.transcript.begin_turn().set_user(text)

    def add_assistant(self, text: str) -> None:
        """一整段模型回复（非流式：历史回填、引擎侧的说明都走这里）。"""
        turn = self.transcript.current_turn()
        turn.begin_reply()
        turn.append_reply(text)
        turn.finish_reply()

    def add_note(self, text: str) -> None:
        """系统提示/活动行（阶段、工具读取、取消）——与对话正文区分开，别读成模型说的话。

        标签按内容分档（读取 / 技能 / 提示）：参照图里这些行前面都有一个小标签（"思考"、"Bash"），
        一律写"提示"等于没写 —— 用户扫一眼就知道那是"读了文件"还是"加载了技能"。
        """
        self.transcript.current_turn().add_activity(_activity_tag(text), text)

    def begin_stream(self, title: str) -> None:
        """开始一段流式输出：后续 append_delta 往这一轮里追加。"""
        self._stream_section = "reply"
        self.transcript.current_turn().begin_reply(title)

    def append_delta(self, text: str) -> None:
        """流式增量：先认**分节标记**（思考 / 回复），再把文字送进对应的小节。

        标记是我们自己插的（`api_client.delta_stream` 在思考与正文之间发一行标题），
        所以一定整段到达、不会跨块 —— 按它分节就能把"思考过程"折起来，而不是让它和答案
        连成一片（用户要求："模型的思考过程你加一个可以折叠和展开"）。
        """
        if not text:
            return
        section = self._stream_section
        for marker, kind in ((THINKING_HEADER, "thinking"), (ANSWER_HEADER, "reply")):
            if marker in text:
                head, _, text = text.partition(marker)
                self._push_delta(head, section)
                section = kind
        self._stream_section = section
        self._push_delta(text, section)
        self.transcript.scroll_to_bottom()

    def _push_delta(self, text: str, section: str) -> None:
        if not text:
            return
        turn = self.transcript.current_turn()
        if section == "thinking":
            turn.append_thinking(text)
        else:
            turn.append_reply(text)

    def _stream_label_ref(self):
        """当前轮里"还在长字"的那个标签（收尾后必须是 None）—— 只给用例与诊断用。"""
        turn = self.transcript.last_turn()
        return None if turn is None else turn._stream_label

    def end_stream(self) -> None:
        """一段流结束：这一轮定稿（正文切分块、显示时间与复制）。

        必须显式收尾：分块渲染只在收尾时做（流式期间每个增量都重排整轮太贵），
        不收尾的话代码块永远显示成一段普通文字。
        """
        turn = self.transcript.last_turn()
        if turn is not None:
            turn.finish_reply()

    def add_error(self, text: str) -> None:
        self.transcript.current_turn().add_error(text)
        self.transcript.scroll_to_bottom()

    def set_status(self, text: str) -> None:
        self.status.setText(text)

    def transcript_text(self) -> str:
        return self.transcript.toPlainText()

    # ── 后端（模式胶囊）──────────────────────────────────────────────────
    def set_backend(self, backend_id: str) -> None:
        """把胶囊切到某个后端（设置页改了后端时同步过来，两处显示不许不一致）。"""
        self._backend_id = str(backend_id or self._backend_id)
        self.mode_button.setText(self._backend_label())

    def _backend_label(self) -> str:
        """胶囊上的字：当前后端的**短名**（取不到就给 id，绝不显示成空白）。

        只用短名：右列在 150% 缩放下只有 400 逻辑像素左右，显示名里的括号说明
        （"内置 agent（直连模型 API）"）会把胶囊撑到被裁字 —— 实测那一刻看到的是
        "置 agent（直连模型 API）"，左边第一个字被切掉。全名留在提示气泡与菜单里，
        想知道细节的人点开就有。
        """
        from ..agent_backends import DEFAULT_BACKEND_ID, backend_descriptor

        backend_id = getattr(self, "_backend_id", "") or DEFAULT_BACKEND_ID
        try:
            name = backend_descriptor(backend_id).display_name
        except Exception:  # noqa: BLE001 - 未知 id 不该把面板拖崩，显示 id 即可
            name = backend_id
        short = name.split("（", 1)[0].split("(", 1)[0].strip() or name
        return f"{short} ▾"

    def _fill_mode_menu(self) -> None:
        from ..agent_backends import available_backends, backend_descriptor

        self.mode_menu.clear()
        current = getattr(self, "_backend_id", "")
        for descriptor in available_backends():
            label = descriptor.display_name
            if descriptor.id == current:
                label = f"✓ {label}"
            action = self.mode_menu.addAction(label)
            action.triggered.connect(
                lambda _checked=False, chosen=descriptor.id: self._choose_backend(chosen)
            )
        self.mode_menu.addSeparator()
        self.mode_menu.addAction("在「控制台 → 设置」里配置后端").triggered.connect(
            lambda _checked=False: self.console_requested.emit()
        )
        try:
            self.mode_button.setToolTip(
                f"当前后端：{backend_descriptor(current).display_name}。"
                "点开可切换（与设置页里的「后端」同一项）。"
            )
        except Exception:  # noqa: BLE001 - 同上
            pass

    def _choose_backend(self, backend_id: str) -> None:
        self.set_backend(backend_id)
        self._fill_mode_menu()
        self.backend_changed.emit(backend_id)

    # ── 内部 ──────────────────────────────────────────────────────────────
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
        # 输入框自己处理 Enter（发送）与 Ctrl+Enter（换行，见 ChatInput）；
        # 这里兜住"焦点不在输入框上但按了 Ctrl+Enter"的情况（焦点在记录区/下拉时）
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter) and (
            event.modifiers() & Qt.KeyboardModifier.ControlModifier
        ) and self.focusWidget() is not self.input:
            self._on_send()
            return
        super().keyPressEvent(event)
