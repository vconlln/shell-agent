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

    assert h1_sizes and max(h1_sizes) > base * 1.2, f"h1 没有放大：{h1_sizes}（基准 {base}）"
    # 上限也钉住：方案预览那一栏只有 300~500px 宽，1.4 倍以上的标题在窄栏里很"吵"
    #（第一版就是 1.45，用户看过截图说"太丑"）。这不是审美洁癖，是版面约束。
    assert max(h1_sizes) <= base * 1.35, f"标题又变大了：{max(h1_sizes)}（基准 {base}）"
    assert h1_weight >= 600, f"标题没有加粗：{h1_weight}"
    # 列表项是普通正文，不该跟着变大
    item_sizes, _, _, _ = _block_info(document, "建一个子目录")
    assert not item_sizes or max(item_sizes) <= base * 1.05


def test_inline_code_is_monospace_and_coloured_but_not_a_box(view):
    """行内代码用"等宽 + 变色"区分，**不给底色**。

    用户看过真实截图后的反馈是"太丑"：Qt 的富文本没有内边距，给它加底色就是一个个
    紧贴字形的小方块 —— 一份方案里到处是 `代码`，整页就变成"满屏高亮块"。
    代码**块**仍然有底色（它是独立的块，四周有留白）。
    """
    from tu_shell_agent.ui.markdown import INLINE_CODE_COLOR

    view.set_markdown(PLAN)
    document = view.document()

    _, _, inline_backgrounds, _ = _block_info(document, "统计")
    assert not inline_backgrounds, "行内代码又带上底色方块了"

    # 等宽 + 变色两条都要在（否则行内代码与正文就分不出来了）
    families, colours = _inline_code_styles(document, "统计")
    assert any("mono" in name.lower() for name in families), f"行内代码不是等宽：{families}"
    assert INLINE_CODE_COLOR.lower() in {colour.lower() for colour in colours}, colours


def test_code_blocks_still_have_a_background(view):
    """围栏代码块仍然有自己的底色（与行内代码区分开）。"""
    view.set_markdown(PLAN)
    _, _, _, block_background = _block_info(view.document(), "backup=")
    assert block_background, "围栏代码块没有底色"


def _inline_code_styles(document, needle: str) -> tuple[list[str], list[str]]:
    """`needle` 所在块里等宽片段的 (字体族, 颜色) 列表。"""
    for index in range(document.blockCount()):
        block = document.findBlockByNumber(index)
        if needle not in block.text():
            continue
        families: list[str] = []
        colours: list[str] = []
        for fragment in _fragments_of(block):
            char_format = fragment.charFormat()
            names = char_format.fontFamilies()
            if hasattr(names, "toStringList"):          # 某些版本给的是 QStringList
                listed = [str(name) for name in names.toStringList()]
            elif isinstance(names, (list, tuple)):      # PySide6 6.11 给的是普通 list
                listed = [str(name) for name in names]
            else:
                listed = []
            if any("mono" in name.lower() for name in listed):
                families.extend(listed)
                colours.append(char_format.foreground().color().name())
        return families, colours
    raise AssertionError(f"文档里没有包含 {needle!r} 的块")


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


def test_document_surface_is_not_near_black(qtbot, restore_app, tmp_path):
    """**渲染出来看**：文档面跟所在卡片同色 —— 不是近黑底、也不是只剩一圈空心边框。

    用户看过截图后明确要求："底不要弄成纯黑的呀，底还原回去"。这里直接比像素：
    方案预览在左栏卡片里（#17181c），报告视图在右栏（#101114）。
    """
    from PySide6.QtCore import QPoint
    from PySide6.QtGui import QColor, QImage, QPainter

    from tu_shell_agent.ui.main_window import MainWindow
    from tu_shell_agent.ui.settings import AppSettings

    plan = tmp_path / "plan.md"
    plan.write_text(PLAN, encoding="utf-8")
    window = MainWindow(
        wire_controller=False,
        settings=AppSettings(run_root=str(tmp_path / "runs"), backdrop="off"),
    )
    qtbot.addWidget(window)
    window.resize(1400, 950)
    window.show()
    window.apply_appearance()
    window.left_pane.set_plan(str(plan))
    window.right_tabs.setCurrentWidget(window.right_pane)     # 右列页签不选中时那页不渲染
    qtbot.wait(40)

    shot = QImage(window.size(), QImage.Format.Format_ARGB32_Premultiplied)
    shot.fill(QColor(255, 0, 255))
    painter = QPainter(shot)
    window.render(painter, QPoint(0, 0))
    painter.end()

    from PySide6.QtGui import QPalette

    near_black = {"#0b0c0e", "#000000"}
    for name, view in (("方案预览", window.left_pane.plan_preview),
                       ("报告视图", window.right_pane.notes_view)):
        origin = view.mapTo(window, view.rect().topLeft())
        inside = shot.pixelColor(origin.x() + 6, origin.y() + view.height() // 2).name()
        assert inside not in near_black, f"{name}的底色又变成近黑了（取到 {inside}）"
        # 机制上也钉住：调色板里不能再有 `bg_under` 那一档的染色（QTextEdit 会拿 Base 填视口）。
        # 顺带记一次教训：曾试图在 PaletteChange 里改调色板来"清底"，结果
        # "设置→事件→再设置"成了环 —— 整个用例套件从 17 分钟变成跑不完。
        # 现在靠 QSS 的 transparent 规则，不再动调色板。
        for role in (QPalette.ColorRole.Base, QPalette.ColorRole.Window):
            tinted = view.viewport().palette().color(role).name()
            assert tinted != "#0b0c0e", f"{name}的 {role.name} 又被染成近黑底了"


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

    # 流式期间是**纯文本**：这一路也可能是脚本契约，边流边按 Markdown 解析会让
    # `# 注释` 在眼前变成巨型标题又缩回去（用户截图里就是满屏错乱的标题）。
    # 定稿时才按内容决定 Markdown 还是代码块。
    streaming = panel.transcript.last_turn().findChildren(QLabel, "chatReplyText")[0]
    assert streaming.textFormat().name == "PlainText", "流式期间不该渲染 Markdown"
    assert streaming.text() == "答案是 **6**，注意 `a_b_c` 是标识符。"

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
    # 思考过程也是"流式纯文本、定稿再分流"：它里面同样常有代码与 `#` 注释。
    # 这一段是散文，所以定稿后应当是 Markdown（换成代码则由
    # `test_chat_rendering.test_thinking_that_is_code_is_shown_as_plain_text` 守着）。
    assert turn.thinking_label.textFormat().name == "PlainText", "流式思考不该渲染 Markdown"
    turn.finish_reply()
    assert turn.thinking_label.textFormat().name == "MarkdownText", "散文型思考定稿后应当是 Markdown"


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


def test_tab_stop_is_four_spaces(view):
    """制表位按 **4 个空格** 算（用户："Tab 的长度有点长，应该是四个空格的长度"）。

    Qt 的默认制表位是 8 个字符宽（≈80px），一份用 tab 缩进的方案在预览里会缩出去一大截。
    """
    view.set_markdown("# 标题\n\n\techo 缩进用 tab\n")
    expected = 4 * view.fontMetrics().horizontalAdvance(" ")
    actual = view.document().defaultTextOption().tabStopDistance()
    assert abs(actual - expected) < 0.5, f"制表位 {actual}px，4 个空格应当是 {expected}px"
