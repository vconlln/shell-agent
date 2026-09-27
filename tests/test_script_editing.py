"""中栏脚本视图：语法高亮 + 手改 + 自动格式化 / 换行符识别。

用户要求（2026-09-20 睡前）："中间这栏的 shell 代码高亮渲染……像 vscode 那种的，
并且还需要自动格式化代码，不论我是删除还是粘贴都自动识别换行符号等等"。

这里钉的是**用户手能触到的那几件事**：粘贴带 CRLF 的脚本会怎样、按回车会怎样、
点「格式化」会怎样、"改后重跑"之前会不会先把脚本整理好并写回界面。
"""

from __future__ import annotations

import pytest
from PySide6.QtCore import Qt
from PySide6.QtGui import QKeyEvent, QShortcut, QTextCursor
from PySide6.QtWidgets import QApplication

from tu_shell_agent.ui.widgets.script_view import ScriptView


@pytest.fixture(scope="module", autouse=True)
def _themed(qapp):
    """主题每个模块只装一次（理由见 `test_chat_rendering.py` 里同名夹具）。"""
    from tu_shell_agent.ui.theme import apply_theme

    apply_theme(qapp)
    yield


@pytest.fixture
def view(qtbot) -> ScriptView:
    widget = ScriptView()
    qtbot.addWidget(widget)
    widget.resize(560, 320)
    widget.show()
    return widget


def _paste(view: ScriptView, text: str) -> None:
    """走真正的粘贴入口（`insertFromMimeData`），而不是直接 setPlainText。"""
    from PySide6.QtCore import QMimeData

    data = QMimeData()
    data.setText(text)
    view.insertFromMimeData(data)


def _press(view: ScriptView, key, modifiers=Qt.KeyboardModifier.NoModifier) -> None:
    QApplication.sendEvent(view, QKeyEvent(QKeyEvent.Type.KeyPress, key, modifiers))


# ── 粘贴：换行符与行首行尾 ────────────────────────────────────────────


def test_paste_normalizes_crlf_and_reports_it(view):
    """"不论我是粘贴都自动识别换行符号"：CRLF 进来就变 LF，并且**说一句**。"""
    notes: list[str] = []
    view.notice.connect(notes.append)

    _paste(view, "#!/usr/bin/env bash\r\nset -euo pipefail\r\necho hi\r\n")

    assert "\r" not in view.toPlainText(), "`\\r` 还在文本里（bash 会报 command not found）"
    assert view.toPlainText().startswith("#!/usr/bin/env bash\nset -euo pipefail\n")
    assert any("CRLF" in note for note in notes), notes


def test_paste_tidies_tabs_and_trailing_spaces(view):
    """粘贴进来的行首 tab 换成 4 空格、行尾空白去掉（与格式化同一套规则）。"""
    _paste(view, "\techo one   \n\t\techo two\n")
    assert view.toPlainText() == "    echo one\n        echo two\n"


def test_paste_without_crlf_says_nothing(view):
    """本来就是干净的 LF：不许谎报"已换行符"（提示要与事实一致）。"""
    notes: list[str] = []
    view.notice.connect(notes.append)
    _paste(view, "echo hi\n")
    assert notes == [], notes


def test_set_text_normalizes_crlf_from_any_source(view):
    """脚本进到视图里时也要归一：它可能来自 CRLF 的历史记录、模板或别处的粘贴。

    （粘贴那条路自己会处理；但 `set_text` 是模型输出、历史回放、模板渲染共用的入口，
    漏了它就会出现"看着一样、跑起来报 `$'\\r': command not found`"。）
    """
    notes: list[str] = []
    view.notice.connect(notes.append)

    view.set_text("#!/usr/bin/env bash\r\nset -euo pipefail\r\necho hi\r\n")

    assert "\r" not in view.toPlainText()
    assert view.toPlainText() == "#!/usr/bin/env bash\nset -euo pipefail\necho hi\n"
    assert any("CRLF" in note for note in notes), notes


def test_plain_text_paste_still_works(view):
    """粘贴普通文本这条路不能因为规范化而失效（它是编辑器最基本的能力）。"""
    _paste(view, "echo 中文与 emoji 🚀\n")
    assert "🚀" in view.toPlainText()


# ── 回车：自动缩进 ────────────────────────────────────────────────────


def test_enter_keeps_the_current_indent(view):
    view.setPlainText("if true; then\n    echo hi")
    view.moveCursor(QTextCursor.MoveOperation.End)
    _press(view, Qt.Key.Key_Return)
    assert view.toPlainText() == "if true; then\n    echo hi\n    "


def test_enter_indents_one_more_level_after_then_and_braces(view):
    view.setPlainText("if true; then")
    view.moveCursor(QTextCursor.MoveOperation.End)
    _press(view, Qt.Key.Key_Return)
    assert view.toPlainText() == "if true; then\n    ", "`then` 之后没有进一档"

    view.setPlainText("backup() {")
    view.moveCursor(QTextCursor.MoveOperation.End)
    _press(view, Qt.Key.Key_Return)
    assert view.toPlainText() == "backup() {\n    ", "`{` 之后没有进一档"


def test_enter_respects_tab_indentation(view):
    """本来用 tab 缩进的脚本继续用 tab —— 不该被塞进空格，那样混着更乱。"""
    view.setPlainText("if true; then\n\techo hi")
    view.moveCursor(QTextCursor.MoveOperation.End)
    _press(view, Qt.Key.Key_Return)
    assert view.toPlainText().endswith("\n\t")


# ── 格式化：按钮、快捷键、结果说明 ────────────────────────────────────


def test_format_now_fixes_the_whole_script_and_says_what_changed(view):
    notes: list[str] = []
    view.notice.connect(notes.append)
    view.setPlainText("if true; then\necho hi\nfi\n")

    changed = view.format_now()

    assert changed is True
    assert view.toPlainText() == "if true; then\n    echo hi\nfi\n"
    assert any("缩进" in note for note in notes), notes


def test_format_now_is_honest_when_there_is_nothing_to_do(view):
    view.setPlainText("echo hi\n")
    notes: list[str] = []
    view.notice.connect(notes.append)

    assert view.format_now() is False
    assert any("没有改动" in note for note in notes), notes


def test_format_shortcut_is_wired(view):
    """Ctrl+Shift+F 要真的触发格式化（按钮之外的那条路）。"""
    view.setPlainText("if true; then\necho hi\nfi\n")
    view.format_shortcut.activated.emit()
    assert view.toPlainText() == "if true; then\n    echo hi\nfi\n"


def test_view_is_editable_but_still_highlights(view):
    """视图现在可以手改（用户要的就是"删除/粘贴"），高亮器仍然挂着。"""
    assert view.isReadOnly() is False
    view.setPlainText("if true; then\n    echo hi\nfi\n")
    QApplication.processEvents()
    block = view.document().findBlockByNumber(0)
    assert block.layout().formats(), "可编辑之后高亮就不生效了"


# ── 中栏与控制器：格式化按钮 + "改后重跑"之前自动格式化 ────────────────


def test_center_pane_has_a_format_button(qtbot, restore_app):
    from tu_shell_agent.ui.panes.center import CenterPane

    pane = CenterPane()
    qtbot.addWidget(pane)
    pane.show_round(1, "if true; then\necho hi\nfi\n")

    pane.format_button.click()

    assert pane.script_view.toPlainText() == "if true; then\n    echo hi\nfi\n"


def test_center_pane_forwards_the_notice(qtbot, restore_app):
    """自动整理的说明要转出去（中栏自己不留一行提示，状态栏才是它该去的地方）。"""
    from tu_shell_agent.ui.panes.center import CenterPane

    pane = CenterPane()
    qtbot.addWidget(pane)
    seen: list[str] = []
    pane.notice.connect(seen.append)

    pane.script_view.format_now()

    assert seen, "格式化之后一句说明都没有"


def test_verify_edited_formats_before_checking_and_writes_it_back(qtbot, tmp_path, monkeypatch):
    """「改后重跑」之前先格式化，并把结果**写回界面**。

    屏幕上的与要送去校验、执行的必须是同一份：自动改动只能发生在看得见的地方 ——
    用户改完脚本点重跑，结果跑的却是"另一份被悄悄整理过的文本"，那是查不出来的怪事。
    """
    from pathlib import Path

    from tu_shell_agent.ui.run_controller import RunController
    from tu_shell_agent.ui.main_window import MainWindow
    from tu_shell_agent.ui.settings import AppSettings

    settings = AppSettings(run_root=str(tmp_path / "runs"), templates_dir=str(tmp_path / "tpl"))
    window = MainWindow(wire_controller=False, settings=settings)
    qtbot.addWidget(window)
    fake = type("Fake", (), {})()
    controller = RunController(
        opencode=fake, toolchain=fake, window=window, run_root=str(tmp_path / "runs"),
    )

    # 用户在界面上改成了"没缩进"的样子
    window.center_pane.show_round(1, "if true; then\necho hi\nfi\n")

    captured: dict = {}
    monkeypatch.setattr(
        controller, "_run",
        lambda entry, run_dir, config, **kwargs: captured.update(run_dir=run_dir, **kwargs),
    )
    controller.verify_edited()

    assert captured, "没有走到「写出脚本并交给引擎」这一步"
    written = sorted(Path(captured["run_dir"]).glob("attempts/*/script.sh"))
    assert written, "没有写出脚本"
    body = written[-1].read_text(encoding="utf-8")
    assert body == "if true; then\n    echo hi\nfi\n", f"送进校验的不是整理过的脚本：{body!r}"
    assert window.center_pane.current_text() == body, "界面上的文本与要跑的文本不一致"
    assert "已先格式化" in window.status_label.text(), window.status_label.text()


def test_verify_edited_refuses_while_a_run_is_in_progress(qtbot, tmp_path):
    """忙碌守卫仍然在格式化**之前**：正在跑的运行目录不许被改写。"""
    from tu_shell_agent.ui.run_controller import RunController
    from tu_shell_agent.ui.main_window import MainWindow
    from tu_shell_agent.ui.settings import AppSettings

    settings = AppSettings(run_root=str(tmp_path / "runs"), templates_dir=str(tmp_path / "tpl"))
    window = MainWindow(wire_controller=False, settings=settings)
    qtbot.addWidget(window)
    fake = type("Fake", (), {})()
    controller = RunController(
        opencode=fake, toolchain=fake, window=window, run_root=str(tmp_path / "runs"),
    )
    window.center_pane.show_round(1, "if true; then\necho hi\nfi\n")

    # 够用的"正在跑"替身：关窗收尾会调用 cancel/wait，缺了它们会报 AttributeError
    controller._worker = type(
        "Busy", (),
        {"isRunning": lambda self: True, "cancel": lambda self: None, "wait": lambda self, _ms=0: None},
    )()
    controller.verify_edited()

    assert "还没结束" in window.status_label.text(), window.status_label.text()
    assert window.center_pane.current_text().startswith("if true; then\necho"), "忙碌时改了界面"


def test_script_view_tab_stop_is_four_spaces(view):
    """中栏脚本视图的制表位也是 4 个空格（与文档面、会话代码块一致）。"""
    from PySide6.QtGui import QFontMetricsF

    # 期望值也用浮点度量：实现里是 `QFontMetricsF`（整数版会差零点几像素）
    expected = 4 * QFontMetricsF(view.document().defaultFont()).horizontalAdvance(" ")
    assert abs(view.tabStopDistance() - expected) < 0.5, (
        f"制表位 {view.tabStopDistance()}px，4 个空格应当是 {expected}px"
    )


# ── 撤销 / 重做 ───────────────────────────────────────────────────────
#
# 用户原话（2026-09-20 睡前）："中间的脚本，我无法使用 ctrl+z 和 ctrl+shift+z 进行撤销操作，
# 请添加上"。这一节钉住的就是这件事，以及它当初为什么会坏。


def _type(view: ScriptView, text: str) -> None:
    """按键盘输入（走 keyPressEvent，是真人在打字会走的那条路）。"""
    for char in text:
        QApplication.sendEvent(
            view,
            QKeyEvent(QKeyEvent.Type.KeyPress, 0, Qt.KeyboardModifier.NoModifier, char),
        )


def _shortcut(view: ScriptView, key, modifiers) -> None:
    """按一个快捷键：先试 QShortcut（真窗口里 Ctrl+Z 是被它接走的）。

    用 `QTest.keyClick` 需要窗口真的被激活、有焦点，无头环境下不可靠；这里直接触发
    挂在控件上的 QShortcut —— 快捷键**有没有绑上**正是要测的东西（绑没绑是用户能感觉到的
    差别：没绑上时按了完全没反应）。
    """
    from PySide6.QtGui import QKeySequence

    wanted = QKeySequence(int(key) | int(modifiers)).toString()
    for shortcut in view.findChildren(QShortcut):
        if shortcut.key().toString() == wanted and shortcut.isEnabled():
            shortcut.activated.emit()
            return
    raise AssertionError(f"没有绑定 {wanted} 这个快捷键")


def test_undo_shortcut_is_bound(view):
    """Ctrl+Z 必须绑在控件上（Qt 自带一份，但用户反馈"按了没反应"，所以显式绑）。"""
    from PySide6.QtGui import QKeySequence

    keys = {shortcut.key().toString() for shortcut in view.findChildren(QShortcut)}
    assert QKeySequence(QKeySequence.StandardKey.Undo).toString() in keys


@pytest.mark.parametrize("sequence", ["Ctrl+Shift+Z", "Ctrl+Y"])
def test_redo_shortcuts_are_bound(view, sequence):
    """重做两个键都要认：Windows 习惯 Ctrl+Y，Linux/GTK 习惯 Ctrl+Shift+Z。

    用户按的是 Ctrl+Shift+Z，而 Qt 默认只给了一个平台的键 —— 这正是"重做没反应"的来源。
    """
    keys = {shortcut.key().toString() for shortcut in view.findChildren(QShortcut)}
    assert sequence in keys


def test_undo_undoes_typing(view):
    """最基本的：打字 → Ctrl+Z 退回去。"""
    _type(view, "echo hi")
    assert view.toPlainText() == "echo hi"

    view.undo()

    assert view.toPlainText() == ""


def test_redo_restores_after_undo(view):
    """Ctrl+Z 之后要能重做回来（Ctrl+Y 与 Ctrl+Shift+Z 都试一遍）。"""
    _type(view, "echo hi")
    view.undo()
    assert view.toPlainText() == ""

    view.redo()

    assert view.toPlainText() == "echo hi"


def test_set_text_is_undoable(view):
    """换一轮脚本（`set_text`）也要能撤销回来。

    **这就是用户踩的那条**：原来 `set_text` 用 `setPlainText()`，它会把撤销栈整个清空 ——
    只要脚本被换过一轮或格式化过一次，之后按 Ctrl+Z 就再也不会发生任何事情
    （实测 `isUndoAvailable()` 直接变 False）。
    """
    view.set_text("echo 第一轮")
    view.set_text("echo 第二轮")
    assert view.toPlainText() == "echo 第二轮"

    # `QPlainTextEdit` 没有 isUndoAvailable()（那是 QTextEdit 的方法），得问文档要
    assert view.document().isUndoAvailable() is True, (
        "换过脚本之后撤销栈是空的 —— 用户按 Ctrl+Z 不会有反应"
    )
    view.undo()

    assert view.toPlainText() == "echo 第一轮"


def test_format_now_is_undoable(view):
    """格式化改错了，Ctrl+Z 要能整篇退回去（否则用户的文本被"自动"改掉且无法挽回）。"""
    view.set_text("if true; then\necho hi\nfi\n")
    assert view.format_now() is True
    formatted = view.toPlainText()

    view.undo()

    assert view.toPlainText() != formatted
    assert "if true; then" in view.toPlainText()


def test_replace_all_does_not_touch_undo_stack_when_text_is_identical(view):
    """正文没变时不产生编辑记录：否则 Ctrl+Z 会退到"什么都没发生"的一步，看着像失灵。"""
    view.set_text("echo hi")
    before = view.document().availableUndoSteps()

    view.replace_all("echo hi")

    assert view.document().availableUndoSteps() == before


def test_repeated_font_change_does_not_rebuild_the_widget(view):
    """字体变化（QSS 生效、换缩放）不许把行号区/高亮器/快捷键重造一遍。

    **这条是真出过事故的**：控件自身的搭建代码曾经被缩进到 `changeEvent` 里面，
    于是每次字体变化都会 `_LineNumberArea(self)` 新建一个行号区、再挂一个
    `ShellHighlighter` 到同一个文档、快捷键也重复绑一份 —— 旧的还连着信号。
    这里直接数一遍：触发两次 FontChange 之后，这些对象必须还是原来那几个。
    """
    from PySide6.QtCore import QEvent

    from tu_shell_agent.ui.widgets.shell_highlight import ShellHighlighter

    area = view._area
    highlighter = view.highlighter
    shortcut_count = len(view.findChildren(QShortcut))

    for _ in range(2):
        QApplication.sendEvent(view, QEvent(QEvent.Type.FontChange))

    assert view._area is area, "行号区被换成了新对象"
    assert view.highlighter is highlighter, "同一个文档上被挂了第二个高亮器"
    assert len(view.findChildren(QShortcut)) == shortcut_count
    # 高亮器与文档是一对一：多挂的那份还会继续格式化文本（两套规则互相打架）
    assert len(view.document().findChildren(ShellHighlighter)) == 1
