"""中栏「文件」页：在左栏文件夹里点一个文件 → 中栏打开编辑 → 保存。

用户要求（2026-09-20）：**"这个方案文档，我还不能编辑，我的意思是可以在底下文件夹选择
文件，并在中间这栏脚本这里进行编辑等等。"**

所以这里钉住的是"点开 → 改 → 保存"这条链上的每一环，尤其是**保存之后方案预览要跟着更新**
（不然用户改完方案、左边还是旧的，运行读到的也是旧正文 —— 那是最难查的一类问题）。
另外钉住"读不了的文件不许装成能编辑"：二进制/非 UTF-8 一旦被当成可编辑文本，用户会把文件写坏。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tu_shell_agent.ui.panes.center import CenterPane
from tu_shell_agent.ui.panes.left import LeftPane
from tu_shell_agent.ui.widgets.plan_tree import PLAN_SUFFIXES


@pytest.fixture(scope="module", autouse=True)
def _themed(qapp):
    """主题每个模块只装一次（理由见 test_chat_rendering.py 里同名夹具）。"""
    from tu_shell_agent.ui.theme import apply_theme

    apply_theme(qapp)
    yield


@pytest.fixture
def center(qtbot) -> CenterPane:
    pane = CenterPane()
    qtbot.addWidget(pane)
    pane.resize(720, 480)
    pane.show()
    return pane


# ── 打开 ──────────────────────────────────────────────────────────────


def test_opening_a_file_shows_it_in_the_file_tab(center: CenterPane, tmp_path: Path):
    """点一个文件 → 中栏切到「文件」页并显示它的内容。"""
    script = tmp_path / "run.sh"
    script.write_text("#!/usr/bin/env bash\necho hello\n", encoding="utf-8")

    assert center.open_path(str(script)) is True

    assert center.tabs.currentIndex() == 2, "没有切到「文件」页"
    assert center.tabs.tabText(2) == "run.sh"
    assert "echo hello" in center.file_view.toPlainText()
    assert center.file_path() == str(script)
    assert center.save_button.isVisible() is True


def test_opening_a_file_with_crlf_normalizes_and_is_not_dirty(center: CenterPane, tmp_path: Path):
    """CRLF 的文件打开时归一成 LF，而且**不算改动**（打开不等于改过）。"""
    script = tmp_path / "win.sh"
    script.write_bytes(b"#!/usr/bin/env bash\r\necho hi\r\n")

    center.open_path(str(script))

    assert "\r" not in center.file_view.toPlainText()
    assert center.file_is_dirty() is False
    assert center.tabs.tabText(2) == "win.sh"


def test_opening_an_empty_path_does_nothing(center: CenterPane):
    assert center.open_path("") is False
    assert center.file_path() == ""


# ── 编辑与保存 ────────────────────────────────────────────────────────


def test_editing_marks_the_tab_and_saving_clears_it(center: CenterPane, tmp_path: Path):
    """改动要看得见（页签上的圆点），保存后消失 —— 用户才知道"存没存上"。"""
    script = tmp_path / "run.sh"
    script.write_text("echo old\n", encoding="utf-8")
    center.open_path(str(script))

    center.file_view.set_text("echo new\n")

    assert center.file_is_dirty() is True
    assert center.tabs.tabText(2).endswith("•")
    assert center.save_file() is True
    assert script.read_text(encoding="utf-8") == "echo new\n"
    assert center.file_is_dirty() is False
    assert center.tabs.tabText(2) == "run.sh"


def test_saving_writes_lf_even_if_the_editor_has_crlf(center: CenterPane, tmp_path: Path):
    """保存统一写 LF：`\\r` 留在脚本里会让 bash 与 shellcheck 报"看着一样的行"出错。"""
    script = tmp_path / "run.sh"
    script.write_text("echo a\n", encoding="utf-8")
    center.open_path(str(script))
    center.file_view.replace_all("echo a\r\necho b\r\n")

    center.save_file()

    raw = script.read_bytes()
    assert b"\r" not in raw
    assert raw.endswith(b"\n")


def test_saving_without_an_open_file_says_what_to_do(center: CenterPane):
    """还没打开文件就按保存：说清楚去哪点，而不是静默什么都不做。"""
    notes: list[str] = []
    center.notice.connect(notes.append)

    assert center.save_file() is False

    assert notes and "先在左栏" in notes[-1], notes


def test_saving_reports_a_write_failure(center: CenterPane, tmp_path: Path):
    """写盘失败（目录被删/没权限）要如实报错并返回 False，不能装作保存成功。"""
    script = tmp_path / "run.sh"
    script.write_text("echo a\n", encoding="utf-8")
    center.open_path(str(script))
    script.unlink()
    script.parent.chmod(0o500)          # 目录只读 → 新建同名文件会失败
    notes: list[str] = []
    center.notice.connect(notes.append)
    try:
        saved = center.save_file()
    finally:
        script.parent.chmod(0o700)

    assert saved is False
    assert notes and "保存失败" in notes[-1], notes
    assert "echo a" in center.file_view.toPlainText(), "失败时不该把编辑器内容清掉"


def test_save_shortcut_is_scoped_to_the_file_view(center: CenterPane, tmp_path: Path):
    """Ctrl+S 绑在文件页上（WidgetShortcut）：别去抢整个窗口的 Ctrl+S。"""
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QKeySequence

    script = tmp_path / "run.sh"
    script.write_text("echo a\n", encoding="utf-8")
    center.open_path(str(script))
    center.file_view.set_text("echo b\n")

    assert center.save_shortcut.key() == QKeySequence(QKeySequence.StandardKey.Save)
    assert center.save_shortcut.context() == Qt.ShortcutContext.WidgetShortcut

    center.save_shortcut.activated.emit()

    assert script.read_text(encoding="utf-8") == "echo b\n"


# ── 读不了的文件不许装成能编辑 ────────────────────────────────────────


def test_binary_file_is_shown_read_only_with_the_reason(center: CenterPane, tmp_path: Path):
    """二进制/非 UTF-8：页面里写清楚为什么打不开，并且**不能保存**。"""
    blob = tmp_path / "blob.bin"
    blob.write_bytes(b"\x00\x01\x02\xff\xfe\x80")
    notes: list[str] = []
    center.notice.connect(notes.append)

    assert center.open_path(str(blob)) is False

    assert center.tabs.currentIndex() == 2
    text = center.file_view.toPlainText()
    assert "打不开" in text and "blob.bin" in text
    assert center.file_view.isReadOnly() is True
    assert center.file_is_dirty() is False
    assert center.save_button.isEnabled() is False
    assert notes and "打不开" in notes[-1]
    assert blob.read_bytes() == b"\x00\x01\x02\xff\xfe\x80", "只读页面绝不许写回"


def test_missing_file_is_reported_too(center: CenterPane, tmp_path: Path):
    assert center.open_path(str(tmp_path / "没有这个文件.md")) is False
    assert "读不了" in center.file_view.toPlainText() or "打不开" in center.file_view.toPlainText()


# ── 格式化按钮跟着"你在看哪一页"走 ────────────────────────────────────


def test_format_button_acts_on_the_visible_page(center: CenterPane, tmp_path: Path):
    """「格式化」必须作用于你正在看的那一篇：在文件页按它时改文件，而不是偷偷改脚本。"""
    script = tmp_path / "run.sh"
    script.write_text("echo a\n", encoding="utf-8")
    center.show_round(1, "if true; then\necho script\nfi\n")
    center.open_path(str(script))
    center.file_view.set_text("if true; then\necho file\nfi\n")

    center._format_current()

    assert "  echo file" in center.file_view.toPlainText(), "文件页没有被格式化"
    assert "echo script" in center.current_text(), "脚本页不该被动到"


# ── 左栏文件树：点任何文件都能打开 ────────────────────────────────────


def _tree_row(tree, folder: Path, name: str):
    index = tree.model.index(str(folder))
    for row in range(tree.model.rowCount(index)):
        child = tree.model.index(row, 0, index)
        if tree.model.fileName(child) == name:
            return child
    raise AssertionError(f"{name} 不在树里")


def test_clicking_any_file_asks_to_open_it(qtbot, tmp_path: Path):
    """点 `run.sh` 这类非方案文件：要**打开编辑**，但**不能**被当成方案。"""
    from PySide6.QtTest import QTest

    from tu_shell_agent.ui.widgets.plan_tree import PlanTree

    plans = tmp_path / "plans"
    plans.mkdir()
    (plans / "run.sh").write_text("echo hi\n", encoding="utf-8")
    tree = PlanTree()
    qtbot.addWidget(tree)
    tree.resize(320, 300)
    tree.show()
    tree.set_root(str(plans))
    for _ in range(160):
        if tree.model.rowCount(tree.model.index(str(plans))):
            break
        QTest.qWait(50)
    opened: list[str] = []
    chosen: list[str] = []
    tree.file_opened.connect(opened.append)
    tree.plan_chosen.connect(chosen.append)

    tree._on_clicked(_tree_row(tree, plans, "run.sh"))

    assert opened == [str(plans / "run.sh")], "点文件没有要求打开它"
    assert chosen == [], ".sh 不是方案文档，不该被设为方案"


def test_clicking_a_plan_file_opens_it_and_sets_the_plan(qtbot, tmp_path: Path):
    """点方案文档：既打开编辑、也设为方案（两件事都要发生）。"""
    from PySide6.QtTest import QTest

    from tu_shell_agent.ui.widgets.plan_tree import PlanTree

    plans = tmp_path / "plans"
    plans.mkdir()
    (plans / "方案.md").write_text("# 方案\n", encoding="utf-8")
    tree = PlanTree()
    qtbot.addWidget(tree)
    tree.resize(320, 300)
    tree.show()
    tree.set_root(str(plans))
    for _ in range(160):
        if tree.model.rowCount(tree.model.index(str(plans))):
            break
        QTest.qWait(50)
    opened: list[str] = []
    chosen: list[str] = []
    tree.file_opened.connect(opened.append)
    tree.plan_chosen.connect(chosen.append)

    tree._on_clicked(_tree_row(tree, plans, "方案.md"))

    assert opened == [str(plans / "方案.md")]
    assert chosen == [str(plans / "方案.md")]
    assert ".md" in PLAN_SUFFIXES


# ── 主窗口：整条链（点开 → 编辑 → 保存 → 方案预览跟着变）────────────────


def test_editing_the_plan_in_the_middle_updates_the_plan_preview(qtbot, tmp_path: Path):
    """端到端：在左栏点方案文档 → 中栏改 → 保存 → 左边预览与后续运行都用新正文。

    这是用户那句"这个方案文档我还不能编辑"的正面回答：现在能改，而且改完**真的生效**。
    """
    from PySide6.QtTest import QTest

    from tu_shell_agent.ui.main_window import MainWindow
    from tu_shell_agent.ui.settings import AppSettings

    plans = tmp_path / "plans"
    plans.mkdir()
    plan = plans / "体检方案.md"
    plan.write_text("# 原始方案\n\n只做 A\n", encoding="utf-8")
    window = MainWindow(
        wire_controller=False,
        settings=AppSettings(run_root=str(tmp_path), plan_tree_root=str(plans)),
    )
    qtbot.addWidget(window)
    window.resize(1400, 900)
    window.show()
    tree = window.left_pane.plan_tree
    for _ in range(200):
        if tree.model.rowCount(tree.model.index(str(plans))):
            break
        QTest.qWait(50)

    tree._on_clicked(_tree_row(tree, plans, "体检方案.md"))
    QTest.qWait(100)
    assert window.center_pane.file_path() == str(plan), "点文件没有在中栏打开它"

    window.center_pane.file_view.set_text("# 改过的方案\n\n改成只做 B\n")
    assert window.center_pane.save_file() is True
    QTest.qWait(100)

    assert plan.read_text(encoding="utf-8").startswith("# 改过的方案")
    assert "只做 B" in window.left_pane.plan_preview.toPlainText(), "预览还是旧的"
    assert window.left_pane.plan_text().strip().startswith("# 改过的方案"), "运行会用旧正文"
    assert "方案文档已更新" in window.status_label.text()


def test_left_pane_forwards_file_opened(qtbot, tmp_path: Path):
    """左栏要把"文件被点开"转出去（主窗口接它去开中栏）。"""
    pane = LeftPane()
    qtbot.addWidget(pane)
    seen: list[str] = []
    pane.file_opened.connect(seen.append)

    pane.plan_tree.file_opened.emit("/tmp/whatever.sh")

    assert seen == ["/tmp/whatever.sh"]


# ── Markdown 文件要渲染（用户："markdown 文件没有渲染"）──────────────────


def test_markdown_file_opens_rendered_with_a_switch_back_to_editing(
    center: CenterPane, tmp_path: Path
):
    """打开 .md 默认给**渲染后的样子**，右上角「编辑」能切回编辑器。"""
    doc = tmp_path / "方案.md"
    doc.write_text("# 标题\n\n正文一段\n", encoding="utf-8")

    center.open_path(str(doc))

    assert center.is_previewing_markdown() is True, "Markdown 打开时应当直接是渲染视图"
    assert center.preview_button.isVisible() is True
    assert center.preview_button.text() == "编辑"
    # 渲染的是 HTML（标题成了 h1），不是把 `# 标题` 原样显示
    html = center.file_preview.toHtml()
    assert "标题" in html and "# 标题" not in center.file_preview.toPlainText()


def test_switching_back_to_editing_shows_the_source(center: CenterPane, tmp_path: Path):
    doc = tmp_path / "方案.md"
    doc.write_text("# 标题\n", encoding="utf-8")
    center.open_path(str(doc))

    assert center.toggle_file_preview() is False      # 切到编辑

    assert center.preview_button.text() == "预览"
    assert center.file_view.toPlainText() == "# 标题\n"
    assert center.save_button.isVisible() is True, "编辑视图要能保存"


def test_preview_renders_the_current_editor_text_not_the_saved_one(
    center: CenterPane, tmp_path: Path
):
    """预览用**编辑器里的当前内容**：改了没保存也要能看效果（用户要的就是边改边看）。"""
    doc = tmp_path / "方案.md"
    doc.write_text("# 旧标题\n", encoding="utf-8")
    center.open_path(str(doc))
    center.toggle_file_preview()                       # 到编辑
    center.file_view.set_text("# 新标题\n\n- 一\n- 二\n")

    center.toggle_file_preview()                       # 回预览

    assert "新标题" in center.file_preview.toPlainText()
    assert "旧标题" not in center.file_preview.toPlainText()
    assert doc.read_text(encoding="utf-8") == "# 旧标题\n", "切到预览不该顺手写盘"


def test_plain_text_file_has_no_preview_button(center: CenterPane, tmp_path: Path):
    """.sh 之类不给「预览」按钮（没有可渲染的东西，摆着只会让人以为坏了）。"""
    script = tmp_path / "run.sh"
    script.write_text("echo hi\n", encoding="utf-8")

    center.open_path(str(script))

    assert center.is_previewing_markdown() is False
    assert center.preview_button.isVisible() is False
    assert center.file_view.toPlainText().strip() == "echo hi"


def test_saving_a_markdown_file_keeps_the_preview_in_sync(center: CenterPane, tmp_path: Path):
    doc = tmp_path / "方案.md"
    doc.write_text("# 一\n", encoding="utf-8")
    center.open_path(str(doc))
    center.toggle_file_preview()
    center.file_view.set_text("# 二\n")

    center.save_file()

    assert doc.read_text(encoding="utf-8") == "# 二\n"
    center.toggle_file_preview()                       # 回预览
    assert "二" in center.file_preview.toPlainText()


# ── 对比上一轮：右键「回退到上一轮」────────────────────────────────────


def test_diff_menu_offers_revert_to_previous_round(center: CenterPane):
    """右键菜单里要有「回退到上一轮」（用户要求："可以右键选择回退上一轮的结果"）。"""
    from tu_shell_agent.ui.widgets import selection_menu

    captured: dict = {}
    real_build = selection_menu.build_menu

    def capture(widget, on_ask, label, *args, **kwargs):
        menu = real_build(widget, on_ask, label, *args, **kwargs)
        captured["menu"] = menu
        return menu

    import pytest as _pytest

    monkeypatch = _pytest.MonkeyPatch()
    monkeypatch.setattr(selection_menu, "build_menu", capture)
    monkeypatch.setattr(selection_menu, "exec_menu", lambda *_a, **_k: None)
    try:
        center.compare_view.customContextMenuRequested.emit(
            center.compare_view.rect().topLeft()
        )
    finally:
        monkeypatch.undo()

    titles = [action.text() for action in captured["menu"].actions()]
    assert "回退到上一轮" in titles, titles


def test_revert_puts_the_previous_round_script_back(center: CenterPane):
    """回退把中栏脚本换成上一轮那一版，并且**可撤销**（Ctrl+Z 能退回来）。"""
    notes: list[str] = []
    center.notice.connect(notes.append)
    center.show_round(1, "echo 第一版\n")
    center.show_round(2, "echo 第二版（改坏了）\n")

    assert center.revert_to_previous() is True

    assert center.current_text() == "echo 第一版\n"
    assert "第 1 轮" in notes[-1] and "改后重跑" in notes[-1]
    center.script_view.undo()
    assert center.current_text() == "echo 第二版（改坏了）\n", "回退必须能撤销"


def test_revert_is_honest_when_there_is_no_previous_round(center: CenterPane):
    notes: list[str] = []
    center.notice.connect(notes.append)
    center.show_round(1, "echo 只有一轮\n")

    assert center.revert_to_previous() is False

    assert "没有上一轮" in notes[-1]
    assert center.current_text() == "echo 只有一轮\n", "没有上一轮时不许动内容"


def test_revert_updates_the_compare_page(center: CenterPane):
    """回退之后对比页要说实话：现在与上一轮**一致**（差异清空），而不是继续显示旧差异。"""
    center.show_round(1, "echo 第一版\n")
    center.show_round(2, "echo 第二版\n")
    assert "第一版" in center.compare_html()

    center.revert_to_previous()

    html = center.compare_html()
    assert "第二版" not in html or "diff-removed" in html, "对比页还在拿旧内容比"
