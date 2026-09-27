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


@pytest.fixture
def chat(qtbot, restore_app):
    from tu_shell_agent.ui.theme import apply_theme

    apply_theme(restore_app)
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
