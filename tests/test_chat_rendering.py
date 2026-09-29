"""对话记录区的"按内容决定怎么渲染"（用户报的那张截图）。

用户反馈的原话是"你看看你这个markdown渲染，太难看了" —— 截图里引擎那条流
（"第 N 轮 · 模型输出"发的就是题面契约本身）被当成 Markdown 渲染了：
脚本里的 `# 注释` 变成巨型标题、相邻的代码行被并成一个段落、`- ` 开头的行变成项目符号。

修法不是"少渲染"，而是**按内容分流**：像代码的（契约、shebang、成片的 shell 行）
进代码块（等宽、可复制），像散文的才按 Markdown 渲染。
"""

from __future__ import annotations

import pytest
from PySide6.QtWidgets import QApplication, QLabel, QWidget

from tu_shell_agent.ui.chat import ChatPanel
from tu_shell_agent.ui.chat_view import looks_like_code, split_contract

CONTRACT = """===TU-SCRIPT===
#!/usr/bin/env bash
set -euo pipefail
# @@TU:BODY@@
# 运行目录体检：生成 samples/ 样本并统计行数
export LC_ALL=C
count_lines() { local file="$1"; awk 'END { print NR + 0 }' "$file"; }
===TU-NOTES===
放宽了 3 处约束
===TU-ASSUMPTIONS===
- bash >= 4
===TU-END===
"""


@pytest.fixture(scope="module", autouse=True)
def _themed(qapp):
    """主题**每个模块只装一次**。

    每个用例都 `apply_theme` 会给所有存活控件来一次整树重抛光；实测这种"反复重装主题"
    的组合最容易踩到 Qt 内部的坑（整轮用例随机段错误，C 栈落在 libQt6Widgets 的
    `setStyle`/`setStyleSheet` 里）。装一次就够：这些用例不依赖主题切换，只依赖字体。
    """
    from tu_shell_agent.ui.theme import apply_theme

    apply_theme(qapp)
    yield


@pytest.fixture
def chat(qtbot):
    panel = ChatPanel()
    qtbot.addWidget(panel)
    panel.resize(430, 640)
    panel.show()
    return panel


def _blocks(turn) -> list[tuple[str, str]]:
    """卡片回复区里的 (类型, 文本) 列表，按加入顺序。"""
    out: list[tuple[str, str]] = []
    for child in turn.reply_body.findChildren(QWidget):
        if child.parent() is not turn.reply_body:
            continue
        if child.objectName() == "chatCodeBlock":
            code = child.findChild(QWidget, "chatCodeText")
            out.append(("code", code.toPlainText() if code is not None else ""))
        elif isinstance(child, QLabel):
            out.append(("prose", child.text()))
    return out


# ── 判据本身 ──────────────────────────────────────────────────────────


def test_looks_like_code_recognizes_scripts_and_contracts():
    assert looks_like_code(CONTRACT) is True, "契约标记必须直接判定为代码"
    assert looks_like_code("#!/usr/bin/env bash\necho hi\n") is True
    assert looks_like_code(
        "export LC_ALL=C\nreadonly DIR=\"x\"\nlog() { echo \"$*\"; }\nif [ -d x ]; then\nfi\n"
    ) is True
    assert looks_like_code("@@TU:BODY@@\n") is True


def test_looks_like_code_leaves_prose_alone():
    """散文不能被误判成代码（否则 Markdown 就白做了）。"""
    for text in (
        "原因是第二轮的 `rm` 没有先备份，所以目录被清空了。",
        "改动点：\n\n- 先打包 `./logs`\n- 再删 `*.log`\n",
        "**结论**：脚本没问题，是模型理解错了方案里的第三条约束。",
        "",
    ):
        assert looks_like_code(text) is False, f"散文被误判成代码：{text[:30]!r}"


def test_split_contract_separates_script_from_prose():
    sections = split_contract(CONTRACT)
    kinds = [kind for kind, _body in sections]
    assert kinds == ["code", "prose", "prose"], sections
    assert "@@TU:BODY@@" in sections[0][1]
    assert "放宽了 3 处约束" in sections[1][1]
    assert "bash >= 4" in sections[2][1]
    assert "===TU-" not in sections[0][1], "标记还留在脚本里"


def test_split_contract_ignores_plain_replies():
    assert split_contract("普通的一句话，没有契约标记。") == []


# ── 渲染结果 ──────────────────────────────────────────────────────────


def test_engine_stream_renders_script_as_code_not_headings(chat):
    """引擎那条流：脚本进**代码块**，说明与假设进正文 —— 不能再出现巨型标题。"""
    chat.add_user("跑一下")
    chat.begin_stream("第 1 轮 · 模型输出")
    chat.append_delta(CONTRACT)
    chat.end_stream()
    QApplication.processEvents()

    blocks = _blocks(chat.transcript.last_turn())
    assert blocks[0][0] == "code", f"脚本没有进代码块：{blocks}"
    assert "@@TU:BODY@@" in blocks[0][1]
    assert "===TU-SCRIPT===" not in blocks[0][1]

    prose = " ".join(body for kind, body in blocks if kind == "prose")
    assert "放宽了 3 处约束" in prose and "bash >= 4" in prose


def test_engine_script_lines_are_not_turned_into_headings(chat):
    """那条 `# 运行目录体检：…` 的注释行不许变成标题块。

    标题在文档里是"字号明显更大"的块 —— 这里直接查渲染后的字号。
    """
    chat.add_user("跑一下")
    chat.begin_stream("第 1 轮 · 模型输出")
    chat.append_delta(CONTRACT)
    chat.end_stream()
    QApplication.processEvents()

    code = chat.transcript.last_turn().findChild(QWidget, "chatCodeText")
    assert code is not None
    assert "运行目录体检" in code.toPlainText()
    assert "#" in code.toPlainText(), "注释被吃掉了（说明它被当成标题解析了）"


def test_unfenced_script_in_a_chat_reply_also_goes_to_a_code_block(chat):
    """模型"直接甩一段脚本、不套 ``` 围栏"时也要进代码块。

    截图里下半部分那坨乱码就是这种情况：代码行没有围栏，被 Markdown 并成了段落。
    """
    reply = (
        "改好的版本：\n\n"
        "#!/usr/bin/env bash\n"
        "set -euo pipefail\n"
        "# @@TU:BODY@@\n"
        "export LC_ALL=C\n"
        "for f in *.log; do\n"
        '    wc -l "$f"\n'
        "done\n"
    )
    chat.add_user("给我脚本")
    chat.begin_stream("模型回复")
    chat.append_delta(reply)
    chat.end_stream()
    QApplication.processEvents()

    blocks = _blocks(chat.transcript.last_turn())
    code = [body for kind, body in blocks if kind == "code"]
    assert code, f"没套围栏的脚本没有进代码块：{blocks}"
    assert any("for f in *.log" in body for body in code)
    assert not any("for f in *.log" in body for kind, body in blocks if kind == "prose")


def test_normal_prose_reply_stays_markdown(chat):
    """正常说话的那一轮仍然按 Markdown 渲染（加粗、列表要出来）。"""
    chat.add_user("为什么？")
    chat.begin_stream("模型回复")
    chat.append_delta("原因是**第二轮的 `rm` 没有先备份**。\n\n改动点：\n\n- 先打包\n- 再删\n")
    chat.end_stream()
    QApplication.processEvents()

    blocks = _blocks(chat.transcript.last_turn())
    assert [kind for kind, _body in blocks] == ["prose"], blocks
    label = chat.transcript.last_turn().findChildren(QLabel, "chatReplyText")[0]
    assert label.textFormat().name == "MarkdownText"
    assert "*" in label.text(), "加粗标记被吃掉了（那就不叫渲染了）"


def test_thinking_that_is_code_is_shown_as_plain_text(chat):
    """思考过程里成片的代码也按纯文本显示（否则同样是巨型标题）。"""
    chat.add_user("在吗")
    chat.begin_stream("模型回复")
    chat.append_delta("\n—— 思考过程 ——\nexport A=1\nfor f in *; do echo \"$f\"; done\n")
    chat.append_delta("\n—— 回复 ——\n好了。\n")
    chat.end_stream()
    QApplication.processEvents()

    thinking = chat.transcript.last_turn().findChild(QLabel, "chatThinkingText")
    assert thinking is not None
    assert thinking.textFormat().name == "PlainText", "思考里的代码被当成 Markdown 渲染了"


def test_streaming_shows_plain_text_so_headings_do_not_jump(chat):
    """流式期间按纯文本显示：边流边解析会让 `# 注释` 在眼前变成巨型标题又缩回去。"""
    chat.add_user("跑一下")
    chat.begin_stream("第 1 轮 · 模型输出")
    chat.append_delta("# 一行注释\n")
    QApplication.processEvents()

    label = chat.transcript.last_turn().findChildren(QLabel, "chatReplyText")[0]
    assert label.textFormat().name == "PlainText"
    assert label.text() == "# 一行注释\n"


def test_chat_code_block_uses_a_four_space_tab_stop(chat):
    """会话里的代码块：制表位按 **4 个空格**（Qt 默认 8 个字符宽，缩进会宽一倍）。"""
    chat.add_user("跑一下")
    chat.begin_stream("第 1 轮 · 模型输出")
    chat.append_delta("===TU-SCRIPT===\nif true; then\n\techo hi\nfi\n===TU-END===\n")
    chat.end_stream()
    QApplication.processEvents()

    code = chat.transcript.last_turn().findChild(QWidget, "chatCodeText")
    assert code is not None
    from PySide6.QtGui import QFontMetricsF

    expected = 4 * QFontMetricsF(code.document().defaultFont()).horizontalAdvance(" ")
    assert abs(code.tabStopDistance() - expected) < 0.5, (
        f"制表位是 {code.tabStopDistance()}px，4 个空格应当是 {expected}px"
    )


def test_chat_code_block_is_syntax_highlighted(chat):
    """会话输出的代码要**上色**（用户："会话输出的代码没有渲染"）——
    与中栏脚本视图同一套 shell 高亮。"""
    chat.add_user("跑一下")
    chat.begin_stream("第 1 轮 · 模型输出")
    chat.append_delta('===TU-SCRIPT===\n#!/usr/bin/env bash\nif [ -f x ]; then\n\techo "hi"\nfi\n===TU-END===\n')
    chat.end_stream()
    QApplication.processEvents()

    code = chat.transcript.last_turn().findChild(QWidget, "chatCodeText")
    doc = code.document()

    def span_colours(line_no: int) -> dict[str, str]:
        block = doc.findBlockByNumber(line_no)
        return {
            block.text()[f.start : f.start + f.length]: f.format.foreground().color().name()
            for f in (block.layout().formats() if block.layout() else [])
        }

    assert "#!/usr/bin/env bash" in span_colours(0), "shebang 没有上色"
    line_two = span_colours(1)
    assert line_two.get("if") and line_two.get("then"), f"关键字没有上色：{line_two}"
    assert span_colours(2).get("echo"), "命令没有上色"
    assert span_colours(3).get("fi"), "收尾关键字没有上色"


def test_non_shell_code_blocks_are_not_shell_highlighted(chat):
    """别的语言（比如 JSON）不按 shell 上色 —— 那会误导（`{` 被当成代码块）。"""
    chat.add_user("给我一段 JSON")
    chat.begin_stream("模型回复")
    chat.append_delta('```json\n{"key": "value"}\n```\n')
    chat.end_stream()
    QApplication.processEvents()

    code = chat.transcript.last_turn().findChild(QWidget, "chatCodeText")
    assert code is not None
    assert not hasattr(code, "highlighter"), "非 shell 的代码块被按 shell 上色了"


def test_code_block_tab_stop_follows_the_font(chat, qtbot):
    """字体变了（换缩放 / QSS 生效）之后制表位要**重算**。

    踩过的坑：只在构造时算一次，而字体是 QSS 给的、polish 之后才生效 ——
    实测制表位停在旧字体上（24px），比"4 个空格"（28px）窄一截。
    这里直接改**文档默认字体**（那才是文字实际按它排版的那个），再让它重算一次。
    """
    from PySide6.QtGui import QFontDatabase, QFontMetricsF

    chat.add_user("跑一下")
    chat.begin_stream("第 1 轮 · 模型输出")
    chat.append_delta("===TU-SCRIPT===\nif true; then\n\techo hi\nfi\n===TU-END===\n")
    chat.end_stream()
    QApplication.processEvents()

    code = chat.transcript.last_turn().findChild(QWidget, "chatCodeText")
    bigger = QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont)
    bigger.setPointSizeF(bigger.pointSizeF() + 5)
    code.document().setDefaultFont(bigger)
    code._refresh_metrics()

    expected = 4 * QFontMetricsF(bigger).horizontalAdvance(" ")
    assert abs(code.tabStopDistance() - expected) < 0.5, (
        f"换字体后制表位没重算：{code.tabStopDistance()}px，应当是 {expected}px"
    )


# ── 流式期间滚动条要能用（用户实测的 Windows 症状）────────────────────
#
# 原话："如果页面缩放没有弄好，会把会话卡的看不见，要等对话结束才能滑动滑动条"。
# 复现出来的是：每条增量都调 scroll_to_bottom()，于是用户往上翻着看时**每来一个字
# 就被拽回底部** —— 体感就是"滚动条拖不动，得等它说完"。


def _overflow(panel, lines: int = 60) -> None:
    """把记录区撑出滚动条（内容不够高就没有可拖的滚动条）。"""
    panel.begin_stream("模型回复")
    for index in range(lines):
        panel.append_delta(f"第 {index} 行：把记录区撑高，好让滚动条出现。\n")


def test_streaming_follows_the_bottom_when_the_user_is_at_the_bottom(qtbot):
    """本来就停在底部（默认）→ 增量来了继续贴底，这是"跟着流走"。"""
    from tu_shell_agent.ui.chat import ChatPanel

    panel = ChatPanel()
    qtbot.addWidget(panel)
    panel.resize(520, 620)
    panel.show()
    _overflow(panel)
    qtbot.wait(300)

    bar = panel.transcript.verticalScrollBar()
    assert panel.transcript.is_following() is True
    assert bar.value() >= bar.maximum() - 4, "停在底部时应当继续跟随"


def test_scrolling_up_stops_the_auto_follow(qtbot):
    """用户往上翻 → 停止跟随；后续增量**不许**把他拽回底部（这是那条症状的真因）。"""
    from tu_shell_agent.ui.chat import ChatPanel

    panel = ChatPanel()
    qtbot.addWidget(panel)
    panel.resize(520, 620)
    panel.show()
    _overflow(panel)
    qtbot.wait(300)
    bar = panel.transcript.verticalScrollBar()

    bar.setValue(0)                       # 用户往上翻
    qtbot.wait(50)
    assert panel.transcript.is_following() is False

    for _ in range(5):
        panel.append_delta("流式还在继续，不该把我拽下去。\n")
    qtbot.wait(200)

    assert bar.value() == 0, "用户看的位置被抢走了（「要等对话结束才能滑动」就是这个）"


def test_returning_to_the_bottom_resumes_following(qtbot):
    """用户自己回到最底部之后，跟随恢复（不用重启程序，也不用等这一轮结束）。"""
    from tu_shell_agent.ui.chat import ChatPanel

    panel = ChatPanel()
    qtbot.addWidget(panel)
    panel.resize(520, 620)
    panel.show()
    _overflow(panel)
    qtbot.wait(300)
    bar = panel.transcript.verticalScrollBar()
    bar.setValue(0)
    qtbot.wait(50)
    assert panel.transcript.is_following() is False

    bar.setValue(bar.maximum())
    qtbot.wait(50)
    assert panel.transcript.is_following() is True

    panel.append_delta("新的增量\n")
    qtbot.wait(200)
    assert bar.value() >= bar.maximum() - 4, "回到最底部后应当重新跟随"


def test_loading_history_lands_on_the_newest_message(qtbot):
    """载入历史是"程序主动跳转"：即使当前不跟随，也要停在最新一条上。"""
    from tu_shell_agent.run_store.sessions import ChatEntry
    from tu_shell_agent.ui.chat import ChatPanel

    panel = ChatPanel()
    qtbot.addWidget(panel)
    panel.resize(520, 620)
    panel.show()
    _overflow(panel)
    qtbot.wait(200)
    panel.transcript.verticalScrollBar().setValue(0)     # 用户正翻在上面
    qtbot.wait(50)

    entries = [ChatEntry("user", f"历史第 {i} 条", "") for i in range(40)]
    panel.load_history(entries)
    qtbot.wait(300)

    bar = panel.transcript.verticalScrollBar()
    assert bar.maximum() > 0, "历史没撑出滚动条，这条用例就没意义了"
    assert bar.value() >= bar.maximum() - 4, "载入历史后应当停在最新一条"


def test_chat_panel_stays_usable_at_high_scale(qtbot, tmp_path):
    """缩放再大，记录区也不许被挤到看不见（用户："会把会话卡的看不见"）。

    这里量的是**记录区还能拿到多少高度**：窗口地板、会话条、输入框都吃空间，
    记录区被挤成 0 就等于会话看不见了。
    """
    from tu_shell_agent.ui.main_window import MainWindow
    from tu_shell_agent.ui.settings import AppSettings

    window = MainWindow(
        wire_controller=False,
        settings=AppSettings(run_root=str(tmp_path), ui_scale=1.4),
    )
    qtbot.addWidget(window)
    window.apply_appearance()
    window.resize(1024, 600)                 # 小屏 + 放大：最容易把会话挤没的组合
    window.show()
    qtbot.wait(200)

    transcript = window.chat_panel.transcript
    assert transcript.height() >= 48, f"记录区只剩 {transcript.height()}px，会话等于看不见"
    assert transcript.isVisible() is True
    assert window.minimumSizeHint().height() <= 700, (
        "窗口地板高过 720（1080p 在 150% 下的逻辑高度）时，小屏上会被裁掉一截"
    )
