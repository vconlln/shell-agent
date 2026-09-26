"""中栏脚本视图：语法高亮 + 手改 + 自动格式化 / 换行符识别。

用户要求（2026-09-20 睡前）："中间这栏的 shell 代码高亮渲染……像 vscode 那种的，
并且还需要自动格式化代码，不论我是删除还是粘贴都自动识别换行符号等等"。

这里钉的是**用户手能触到的那几件事**：粘贴带 CRLF 的脚本会怎样、按回车会怎样、
点「格式化」会怎样、"改后重跑"之前会不会先把脚本整理好并写回界面。
"""

from __future__ import annotations

import pytest
from PySide6.QtCore import Qt
from PySide6.QtGui import QKeyEvent, QTextCursor
from PySide6.QtWidgets import QApplication

from tu_shell_agent.ui.widgets.script_view import ScriptView


@pytest.fixture
def view(qtbot, restore_app) -> ScriptView:
    from tu_shell_agent.ui.theme import apply_theme

    apply_theme(restore_app)
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
