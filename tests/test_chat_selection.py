"""选中代码 → 引用 → 提问（用户要求："对话也可以选中代码进行对话，询问代码"）。

契约有三条，缺一条这个功能就是假的：
1. 选中的文本要**原样**进入发给模型的那条消息（且新行是 `\\n`，不是 Qt 的 U+2029）；
2. 引用是"这一条消息"的上下文，发出去就撤掉，不能粘在下一条问题上；
3. 用户能在记录里看到自己实际发了什么（引用不是隐藏注入）。
"""

from __future__ import annotations

import pytest
from PySide6.QtCore import QPoint
from PySide6.QtGui import QTextCursor
from PySide6.QtWidgets import QApplication

from tu_shell_agent.ui.chat import ChatPanel
from tu_shell_agent.ui.main_window import MainWindow
from tu_shell_agent.ui.settings import AppSettings

SCRIPT = "#!/bin/bash\nset -euo pipefail\necho one\necho two\n"
_LINE3 = SCRIPT.index("echo one")                 # 第 3 行的起点
_LINE4_END = SCRIPT.index("echo two") + len("echo two")   # 第 4 行的终点


def _select(view, start: int, end: int) -> None:
    """按字符位置选中一段（和鼠标拖动等价的那件事）。"""
    cursor = view.textCursor()
    cursor.setPosition(start)
    cursor.setPosition(end, QTextCursor.MoveMode.KeepAnchor)
    view.setTextCursor(cursor)


# ── 控件层：选中什么就说什么 ──────────────────────────────────────────


def test_script_view_reports_selection_with_the_line_range(qtbot):
    """选中第 3~4 行 → 正文是这两行，来源写着「本轮脚本 · 第 3–4 行」。"""
    from tu_shell_agent.ui.widgets.script_view import ScriptView

    view = ScriptView()
    qtbot.addWidget(view)
    view.set_text(SCRIPT)
    _select(view, _LINE3, _LINE4_END)          # 从 "echo one" 的开头到 "echo two" 的结尾

    assert view.selected_code() == "echo one\necho two"
    assert view.selection_source() == "本轮脚本 · 第 3–4 行"


def test_script_view_says_single_line_as_one_line(qtbot):
    """只选一行时不写区间（"第 3–3 行"读起来是错的）。"""
    from tu_shell_agent.ui.widgets.script_view import ScriptView

    view = ScriptView()
    qtbot.addWidget(view)
    view.set_text(SCRIPT)
    _select(view, _LINE3, _LINE3 + len('echo one'))

    assert view.selection_source() == "本轮脚本 · 第 3 行"


def test_selection_newlines_are_real_newlines(qtbot):
    """Qt 的 `selectedText()` 用 U+2029 当换行 —— 不转换的话发给模型的是一整行长文本。"""
    from tu_shell_agent.ui.widgets.script_view import ScriptView

    view = ScriptView()
    qtbot.addWidget(view)
    view.set_text(SCRIPT)
    _select(view, _LINE3, _LINE4_END)

    assert "\u2029" not in view.selected_code()
    assert len(view.selected_code().split("\n")) == 2


# ── 右键菜单：有选中才出现「提问」 ────────────────────────────────────


def test_menu_offers_the_ask_action_only_with_a_selection(qtbot):
    """没选中内容时菜单里不该出现「就选中的代码提问」（点了也没东西可问）。"""
    from tu_shell_agent.ui.widgets.script_view import ScriptView
    from tu_shell_agent.ui.widgets.selection_menu import build_menu

    view = ScriptView()
    qtbot.addWidget(view)
    view.set_text(SCRIPT)
    label = "就选中的代码提问"

    empty = build_menu(view, lambda _text: None, label)
    assert label not in [action.text() for action in empty.actions()]

    _select(view, _LINE3, _LINE4_END)
    seen: list[str] = []
    menu = build_menu(view, seen.append, label)
    assert menu.actions()[0].text() == label, "「提问」应当是菜单第一项（复制/全选之后才找半天）"
    menu.actions()[0].trigger()
    assert seen == ["echo one\necho two"]


# ── 对话面板：引用进消息、发完即撤 ────────────────────────────────────


@pytest.fixture
def chat(qtbot):
    panel = ChatPanel()
    qtbot.addWidget(panel)
    panel.resize(520, 420)
    panel.show()
    qtbot.waitExposed(panel)
    yield panel
    panel.close()


def test_quote_is_shown_and_then_sent_with_the_question(chat, qtbot):
    """引用条出现 → 发送时引用与问题在同一条消息里 → 记录里能看见 → 引用被撤掉。"""
    sent: list[str] = []
    chat.send_requested.connect(sent.append)

    chat.set_quote("echo one\necho two", "本轮脚本 · 第 3–4 行")
    assert chat.has_quote()
    assert chat.quote_bar.isVisible(), "引用条没显示出来"
    assert "本轮脚本 · 第 3–4 行" in chat.quote_label.text()
    assert "2 行" in chat.quote_label.text()

    chat.input.setPlainText("这两行为什么要分开写？")
    chat._on_send()

    assert len(sent) == 1
    message = sent[0]
    assert "这两行为什么要分开写？" in message
    assert "echo one\necho two" in message
    assert "本轮脚本 · 第 3–4 行" in message
    assert message.startswith("关于以下引用内容"), f"消息没有引用头：{message[:40]!r}"
    assert "~~~" in message, "引用要用 ~~~ 围栏（脚本里本来就可能出现三个反引号）"

    assert "这两行为什么要分开写？" in chat.transcript_text(), "记录里看不到自己发了什么"
    assert not chat.has_quote(), "发出去之后引用应当撤掉，不能粘在下一条问题上"
    assert not chat.quote_bar.isVisible()


def test_message_is_untouched_without_a_quote(chat):
    """没有引用时，发给模型的就是用户敲的那句话本身（一个字符都不许改）。"""
    sent: list[str] = []
    chat.send_requested.connect(sent.append)

    chat.input.setPlainText("解释一下这份报告")
    chat._on_send()

    assert sent == ["解释一下这份报告"]


def test_long_quote_is_truncated_and_the_message_says_so(chat):
    """整份脚本被选中时按上限截断，并且在消息里写明截断（不悄悄少发一段）。"""
    from tu_shell_agent.ui.chat import _QUOTE_MAX_LINES

    chat.set_quote("\n".join(f"echo {index}" for index in range(400)), "本轮脚本 · 全选")
    text, _source = chat.quote()
    assert len(text.split("\n")) == _QUOTE_MAX_LINES
    assert "已截断" in chat.quote_label.text()

    sent: list[str] = []
    chat.send_requested.connect(sent.append)
    chat.input.setPlainText("整体看有什么问题？")
    chat._on_send()
    assert "引用已截断" in sent[0]


def test_clear_quote_button_removes_it(chat, qtbot):
    """「取消引用」要真的撤掉引用（否则用户只能靠发一条空消息来清）。"""
    chat.set_quote("echo one", "对话记录")
    chat.quote_clear_button.click()
    qtbot.wait(20)

    assert not chat.has_quote()
    assert not chat.quote_bar.isVisible()


def test_record_selection_is_quoted(chat, qtbot, monkeypatch):
    """记录区里选中的内容也能提问（追问模型上一句里的某段代码）。"""
    from tu_shell_agent.ui.widgets import selection_menu

    record = "模型：先备份再删除。\n你：为什么？"
    chat.transcript.setPlainText(record)
    _select(chat.transcript, 0, 6)

    menu = _open_context_menu(monkeypatch, chat.transcript)
    assert menu.actions()[0].text() == "就选中的内容提问"
    menu.actions()[0].trigger()

    text, source = chat.quote()
    assert text == record[:6]
    assert source == "对话记录"
    assert chat.focusWidget() is chat.input, "引用之后焦点应当在输入框（接着打字）"


# ── 整窗：中栏选中 → 右栏对话 ────────────────────────────────────────


def _open_context_menu(monkeypatch, widget):
    """触发控件的右键菜单并把菜单对象取出来。

    `exec` 是模态循环，必须拦掉 —— 而且只能拦 `selection_menu.exec_menu` 这一层：
    PySide6 的 C++ 方法不能被 monkeypatch（实测给 `QMenu.exec` 赋值被静默忽略，
    第一版就是这么挂住的：测试在模态菜单里等了一辈子）。
    """
    from tu_shell_agent.ui.widgets import selection_menu

    captured: dict = {}
    real_build = selection_menu.build_menu

    def capture(target, on_ask, label):
        menu = real_build(target, on_ask, label)
        captured["menu"] = menu
        return menu

    monkeypatch.setattr(selection_menu, "build_menu", capture)
    monkeypatch.setattr(selection_menu, "exec_menu", lambda *_args, **_kwargs: None)
    widget.customContextMenuRequested.emit(QPoint(4, 4))
    return captured["menu"]


@pytest.fixture
def window(qtbot, tmp_path):
    app = QApplication.instance()
    previous_sheet = app.styleSheet()
    from tu_shell_agent.ui.theme import apply_theme

    apply_theme(app)
    try:
        win = MainWindow(
            wire_controller=False,
            settings=AppSettings(run_root=str(tmp_path / "runs"), templates_dir=str(tmp_path / "tpl")),
        )
        qtbot.addWidget(win)
        win.resize(1440, 900)
        win.show()
        qtbot.waitExposed(win)
        yield win
    finally:
        app.setStyleSheet(previous_sheet)


def test_center_selection_reaches_the_chat_panel(window, qtbot, monkeypatch):
    """整条链：中栏选中 → 右键提问 → 右栏自动切到「模型对话」并带上引用。"""
    window.center_pane.script_view.set_text(SCRIPT)
    _select(window.center_pane.script_view, _LINE3, _LINE4_END)

    menu = _open_context_menu(monkeypatch, window.center_pane.script_view)
    assert menu.actions()[0].text() == "就选中的代码提问"
    menu.actions()[0].trigger()
    qtbot.wait(20)

    assert window.right_tabs.currentWidget() is window.chat_panel, "没有把对话面板切到前台"
    text, source = window.chat_panel.quote()
    assert text == "echo one\necho two"
    assert source == "本轮脚本 · 第 3–4 行"
    assert "已引用" in window.chat_panel.status.text()


def test_diff_selection_reaches_the_chat_panel(window, qtbot, monkeypatch):
    """对比页（diff）里选中的片段同样能问 —— 读 diff 时最常问"这行为什么要改"。"""
    window.center_pane.show_round(1, SCRIPT)
    window.center_pane.show_round(2, SCRIPT.replace("echo two", "echo THREE"))
    window.center_pane.tabs.setCurrentIndex(1)
    qtbot.wait(20)

    compare = window.center_pane.compare_view
    compare.moveCursor(QTextCursor.MoveOperation.Start)
    for _ in range(3):
        compare.moveCursor(QTextCursor.MoveOperation.Down, QTextCursor.MoveMode.KeepAnchor)

    menu = _open_context_menu(monkeypatch, compare)
    labels = [action.text() for action in menu.actions()]
    assert "就选中的差异提问" in labels
    menu.actions()[0].trigger()
    qtbot.wait(20)

    text, source = window.chat_panel.quote()
    assert text.strip(), "差异页的选中内容没有传过去"
    assert "对比" in source or "提议" in source


def test_quote_bar_does_not_squeeze_the_input(chat, qtbot):
    """引用条出现时不许把输入框挤没（工具区矮的时候这类"加一行"最容易出问题）。"""
    input_height = chat.input.height()
    transcript_before = chat.transcript.height()

    chat.set_quote("echo one\necho two", "本轮脚本 · 第 3–4 行")
    qtbot.wait(20)

    assert chat.input.isVisible(), "引用条把输入框挤掉了"
    assert chat.input.height() == input_height, "输入框被引用条压扁了"
    assert chat.transcript.height() <= transcript_before, "引用条不该让记录区变高（它是被挤的那一方）"
    assert chat.transcript.height() >= chat.transcript.minimumHeight()

    chat.clear_quote()
    qtbot.wait(20)
    assert chat.transcript.height() == transcript_before, "撤掉引用后记录区没收回那一段高度"


def test_crlf_quote_is_normalized(chat):
    """Windows 来的文本可能是 CRLF：引用要规范化成 `\n` 再进提示词。

    `\r` 混在围栏代码块里会让 diff、行数与围栏判定全都变脏（Qt 控件那条路已经换成 `\n`，
    但 `set_quote()` 是公开入口，别的调用方不一定经过控件 —— Windows 上尤其如此）。
    """
    sent: list[str] = []
    chat.send_requested.connect(sent.append)
    chat.set_quote("echo one\r\necho two\r\n", "本轮脚本 · 第 1–2 行")
    text, _source = chat.quote()
    assert text == "echo one\necho two"
    assert "\r" not in text

    chat.input.setPlainText("这两行有问题吗？")
    chat._on_send()
    assert "\r" not in sent[0], f"消息里还带着 \\r：{sent[0]!r}"
