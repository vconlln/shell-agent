"""Markdown 渲染（`ui/markdown.py`）与"思考过程可折叠"的用例。

用户 2026-09-20 的要求：

1. "基本上所有的对话框都没有 markdown 渲染，这个需要加上，不然看起来特别不好看"；
2. "特别是选择方案后展示的这个框没有渲染"（左栏方案预览）；
3. "还有模型对话框也没有渲染"；
4. "模型对话框模型的思考过程你加一个可以折叠和展开"。

这里的断言都落在**渲染出来的结构**上（标题字号、加粗权重、代码块底色、表格边框），
不是"调用了某个方法" —— 换实现（比如将来改成自己写解析器）也该照样通过。
"""

from __future__ import annotations

import pytest
from PySide6.QtGui import QTextTable
from PySide6.QtWidgets import QApplication, QLabel, QWidget

from tu_shell_agent.ui.markdown import MarkdownBrowser, base_point_size, theme_document

PLAN = """# 运行目录体检报告

在**当前目录**里做下面这些事：

1. 建一个子目录 `samples/`
2. 统计 `samples/*.log` 的**行数**

> 注意：`c.txt` 不是 `.log` 文件

| 文件 | 行数 |
| --- | --- |
| a.log | 3 |

```bash
backup="/tmp/logs-$(date +%s).tar.gz"
```

- 只允许写 `samples/` 与 `report.md`
"""


def _fragments_of(block):
    iterator = block.begin()
    while not iterator.atEnd():
        fragment = iterator.fragment()
        if fragment.isValid():
            yield fragment
        iterator += 1


def _block_info(document, needle: str):
    """找到包含 `needle` 的块，返回它的 (字号集合, 最粗权重, 片段底色集合, 块底色)。"""
    for index in range(document.blockCount()):
        block = document.findBlockByNumber(index)
        if needle not in block.text():
            continue
        sizes, weights, backgrounds = set(), set(), set()
        for fragment in _fragments_of(block):
            char_format = fragment.charFormat()
            if char_format.fontPointSize():
                sizes.add(round(char_format.fontPointSize(), 1))
            weights.add(int(char_format.fontWeight()))
            if char_format.background().style().name != "NoBrush":
                backgrounds.add(char_format.background().color().name())
        block_background = block.blockFormat().background()
        block_name = (
            block_background.color().name()
            if block_background.style().name != "NoBrush"
            else ""
        )
        return sizes, (max(weights) if weights else 0), backgrounds, block_name
    raise AssertionError(f"文档里没有包含 {needle!r} 的块：{document.toPlainText()[:80]!r}")


@pytest.fixture
def view(qtbot, restore_app):
    from tu_shell_agent.ui.theme import apply_theme

    apply_theme(restore_app)
    browser = MarkdownBrowser()
    qtbot.addWidget(browser)
    browser.resize(480, 640)
    browser.show()
    return browser


def test_headings_get_real_sizes_and_bold(view):
    """标题要有**明确的字号层级**，不能只靠 Qt 那套相对关键字。"""
    view.set_markdown(PLAN)
    document = view.document()
    base = base_point_size()

    h1_sizes, h1_weight, _, _ = _block_info(document, "运行目录体检报告")
    h2_sizes, h2_weight, _, _ = _block_info(document, "注意：")

    assert h1_sizes and max(h1_sizes) > base * 1.3, f"h1 没有放大：{h1_sizes}（基准 {base}）"
    assert h1_weight >= 600, f"标题没有加粗：{h1_weight}"
    # 列表项是普通正文，不该跟着变大
    item_sizes, _, _, _ = _block_info(document, "建一个子目录")
    assert not item_sizes or max(item_sizes) <= base * 1.05


def test_inline_code_and_code_blocks_get_a_background(view):
    """行内代码与围栏代码块都要有自己的底色 —— 深色主题里没底色等于看不出是代码。"""
    view.set_markdown(PLAN)
    document = view.document()

    _, _, inline_backgrounds, _ = _block_info(document, "统计")
    assert inline_backgrounds, "行内代码没有底色"

    _, _, _, block_background = _block_info(document, "backup=")
    assert block_background, "围栏代码块没有底色"


def test_bold_text_is_actually_bold(view):
    """`**加粗**` 要真的变粗（不能把星号原样显示出来）。"""
    view.set_markdown("这是 **重点** 内容。")
    _, weight, _, _ = _block_info(view.document(), "这是")
    assert weight >= 600, f"加粗没生效：{weight}"
    assert "**" not in view.toPlainText(), "星号被原样显示了 —— 等于没渲染"


def test_table_gets_borders_and_a_header(view):
    """表格要有边框与表头底色（Qt 默认那套黑框在深色主题里几乎看不见）。"""
    view.set_markdown(PLAN)
    document = view.document()
    tables = [frame for frame in document.rootFrame().childFrames() if isinstance(frame, QTextTable)]
    assert tables, "Markdown 表格没有被解析成表格"
    table = tables[0]
    assert (table.rows(), table.columns()) == (2, 2)
    assert table.format().border() == 1, "表格没有边框"
    header_background = table.cellAt(0, 0).format().background()
    assert header_background.style().name != "NoBrush", "表头没有底色"


def test_plain_text_is_kept_for_selection_and_quoting(view):
    """渲染之后仍然能取回纯文本（选中→提问、复制这条路不能因为富文本而断）。"""
    view.set_markdown(PLAN)
    text = view.toPlainText()
    assert "运行目录体检报告" in text and "backup=" in text
    assert "**" not in text and "```" not in text, "标记没被解析掉"


def test_source_is_kept_and_font_change_rerenders(view):
    """源文留一份，字体变了按新字号重渲染（改 ui_scale 后标题层级不能停在旧字号上）。"""
    view.set_markdown("# 标题\n\n正文")
    assert view.markdown() == "# 标题\n\n正文"

    from PySide6.QtCore import QEvent
    from PySide6.QtGui import QFont

    before = view.document().findBlockByNumber(0).blockFormat().headingLevel()
    font = QFont(view.font())
    font.setPointSizeF(base_point_size() + 4)
    view.setFont(font)
    view.changeEvent(QEvent(QEvent.Type.FontChange))     # 无事件循环时 setFont 不一定派发
    after_sizes, _, _, _ = _block_info(view.document(), "标题")
    assert after_sizes and max(after_sizes) > base_point_size() * 1.3, (
        f"字体变大之后标题没有跟着重排：{after_sizes}"
    )
    assert before == 1, "标题层级丢了"


def test_set_plain_shows_markers_verbatim(view):
    """`set_plain` 是不渲染的那条路：提示语里的 `*`、反引号是普通字符，不许被吃掉。"""
    view.set_plain("读不到方案文档：2 * 3 与 a_b_c")
    assert view.toPlainText() == "读不到方案文档：2 * 3 与 a_b_c"


def test_theme_document_is_safe_on_an_empty_document():
    """空文档不许崩（面板构造时就会渲染一次空内容）。"""
    from PySide6.QtGui import QTextDocument

    theme_document(QTextDocument())      # 不抛异常即通过


# ── 接进界面的三处 ────────────────────────────────────────────────────


def test_plan_preview_renders_markdown(qtbot, restore_app, tmp_path):
    """左栏方案预览按 Markdown 渲染（用户点名的那个框）。"""
    from tu_shell_agent.ui.panes.left import LeftPane

    plan = tmp_path / "plan.md"
    plan.write_text(PLAN, encoding="utf-8")
    pane = LeftPane()
    qtbot.addWidget(pane)
    pane.set_plan(str(plan))

    assert isinstance(pane.plan_preview, MarkdownBrowser), "方案预览不是 Markdown 视图"
    sizes, weight, _, _ = _block_info(pane.plan_preview.document(), "运行目录体检报告")
    assert sizes and max(sizes) > base_point_size() * 1.3, "方案标题没有渲染成标题"
    assert "**" not in pane.plan_preview.toPlainText(), "** 原样显示 = 没渲染"
    # 读不到方案时走纯文本那条路（错误消息里的字符不许被当语法）
    pane.set_plan(str(tmp_path / "不存在.md"))
    assert "读不到方案文档" in pane.plan_preview.toPlainText()


def test_notes_view_renders_markdown(qtbot, restore_app):
    """右栏报告视图按 Markdown 渲染（模型写的取舍说明常带列表与行内代码）。"""
    from tu_shell_agent.ui.panes.right import RightPane

    pane = RightPane()
    qtbot.addWidget(pane)
    pane.render_notes("放宽了 **3 处**约束：\n\n- 不再校验行数\n- 允许覆盖", ("bash ≥ 4",))

    assert isinstance(pane.notes_view, MarkdownBrowser)
    text = pane.notes_view.toPlainText()
    assert "放宽了 3 处约束" in text and "**" not in text, "取舍说明没有渲染"
    assert "bash ≥ 4" in text, "假设列表丢了"
    _, weight, _, _ = _block_info(pane.notes_view.document(), "放宽了")
    assert weight >= 600, "加粗没生效"


def test_chat_reply_is_markdown_but_user_text_is_not(qtbot, restore_app):
    """对话里：**模型说的话按 Markdown 渲染，用户敲的字原样显示**。

    用户输入里的 `2 * 3`、`a_b_c`、`_下划线_` 被当语法吃掉才是真的难用；
    而模型的 `**加粗**`、列表、行内代码必须渲染出来。
    """
    from tu_shell_agent.ui.chat import ChatPanel

    panel = ChatPanel()
    qtbot.addWidget(panel)
    panel.add_user("算一下 2 * 3 和 a_b_c 的关系")
    panel.begin_stream("模型回复")
    panel.append_delta("答案是 **6**，注意 `a_b_c` 是标识符。")

    # 流式期间那个标签也要是 Markdown（两处都要：流式块与定稿后的分块正文，
    # 只改一处会出现"流式时显示星号、答完才渲染"的闪变）
    streaming = panel.transcript.last_turn().findChildren(QLabel, "chatReplyText")[0]
    assert streaming.textFormat().name == "MarkdownText", "流式正文没有按 Markdown 渲染"

    panel.end_stream()
    turn = panel.transcript.last_turn()
    user = turn.findChild(QLabel, "chatUserText")
    reply = turn.findChildren(QLabel, "chatReplyText")[0]
    assert user.textFormat().name == "PlainText", "用户的消息不该被渲染"
    assert "*" in user.text(), "用户输入的星号被吃掉了"
    assert reply.textFormat().name == "MarkdownText", "模型回复没有按 Markdown 渲染"


# ── 思考过程：折叠 / 展开 ─────────────────────────────────────────────


def _turn_with_thinking(qtbot, restore_app):
    from tu_shell_agent.ui.chat import ChatPanel

    panel = ChatPanel()
    qtbot.addWidget(panel)
    panel.resize(430, 560)
    panel.show()
    panel.add_user("在吗")
    panel.begin_stream("模型回复")
    panel.append_delta("\n—— 思考过程 ——\n先看目录，**再**统计行数。")
    return panel, panel.transcript.last_turn()


def test_thinking_is_expanded_while_the_model_is_thinking(qtbot, restore_app):
    """还在想的时候默认**展开**：用户要看得见它在想什么（这是直连 API 的卖点之一）。"""
    _panel, turn = _turn_with_thinking(qtbot, restore_app)
    header = turn.findChild(QWidget, "chatThinkingHeader")
    assert turn.thinking_open() is True
    assert "▾" in header.text() and "思考过程" in header.text()
    assert "思考中" in header.text(), f"没有「还在想」的信号：{header.text()}"
    assert turn.thinking_label.textFormat().name == "MarkdownText", "思考过程也要渲染 Markdown"


def test_thinking_collapses_when_the_answer_starts_and_can_be_reopened(qtbot, restore_app):
    """答案一开始就**自动收起**，点标题行能再展开、再收起。"""
    panel, turn = _turn_with_thinking(qtbot, restore_app)
    header = turn.findChild(QWidget, "chatThinkingHeader")

    panel.append_delta("\n—— 回复 ——\n好的，脚本如下。")
    assert turn.thinking_open() is False, "正文开始了思考还摊着"
    assert "▸" in header.text() and "思考中" not in header.text()

    header.click()
    assert turn.thinking_open() is True, "点标题行没有展开"
    assert "▾" in header.text()

    header.click()
    assert turn.thinking_open() is False, "再点一下没有收起"


def test_thinking_and_the_answer_are_separate_sections(qtbot, restore_app):
    """思考与正文要分到两个小节里，不能连成一段（否则折叠起来会把答案也折进去）。"""
    panel, turn = _turn_with_thinking(qtbot, restore_app)
    panel.append_delta("\n—— 回复 ——\n这是答案。")
    panel.end_stream()

    assert "先看目录" in turn.thinking_label.text()
    assert "先看目录" not in "".join(
        label.text() for label in turn.findChildren(QLabel, "chatReplyText")
    ), "思考内容混进正文里了"
    assert "这是答案" in "".join(
        label.text() for label in turn.findChildren(QLabel, "chatReplyText")
    )


def test_thinking_stays_in_the_plain_text_contract(qtbot, restore_app):
    """纯文本回读仍然包含思考过程（与旧版一致：抠脚本看的是"最后一段围栏"）。"""
    panel, turn = _turn_with_thinking(qtbot, restore_app)
    panel.append_delta("\n—— 回复 ——\n```bash\necho hi\n```\n")
    panel.end_stream()

    text = panel.transcript_text()
    assert "思考过程" in text and "先看目录" in text
    from tu_shell_agent.ui.chat import extract_last_script

    assert extract_last_script(text) == "echo hi"


def test_a_reply_without_thinking_has_no_thinking_section(qtbot, restore_app):
    """没有思考过程时不该凭空出现一个折叠小节。"""
    from tu_shell_agent.ui.chat import ChatPanel

    panel = ChatPanel()
    qtbot.addWidget(panel)
    panel.add_user("在吗")
    panel.begin_stream("模型回复")
    panel.append_delta("\n—— 回复 ——\n直接给答案。")
    panel.end_stream()

    turn = panel.transcript.last_turn()
    assert turn.thinking_open() is True                     # 状态是"开"，但没有内容
    assert not turn.thinking_label.text(), "没有思考却出现了思考正文"
    assert turn._thinking_text == "", "没有思考却记下了思考内容"
