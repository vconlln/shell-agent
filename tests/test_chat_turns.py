"""对话记录区改成"每一轮一块"之后的用例（`ui/chat_view.py` + `ui/chat.py` 的输入卡片）。

用户 2026-09-20 的要求（附了两张参照图）：

1. "每一轮会话都在一块，看不清楚" → 每轮一张卡片：用户块与回复块分开、卡内有活动行、
   右下角有时间与复制；
2. 输入卡片按参照图的形状：圆角外框 + 底部一行（＋ / 模式胶囊 / 模型 / 圆形发送）；
3. **Enter 发送、Ctrl+Enter 换行**。

这里钉的都是"能看见的行为"：轮次是否真的分开、代码块有没有单独成块、复制按钮复制的是不是
这一轮的全文、纯文本契约（抠脚本靠它）有没有变。
"""

from __future__ import annotations

import json

import pytest
from PySide6.QtWidgets import QApplication, QLabel, QWidget

from tu_shell_agent.ui.chat import ChatPanel, extract_last_script
from tu_shell_agent.ui.chat_view import split_segments

REPLY = (
    "原因是第二轮的 rm 没有先备份。改好的版本如下：\n\n"
    "```bash\n#!/usr/bin/env bash\nset -euo pipefail\n"
    'backup="/tmp/logs-$(date +%s).tar.gz"\ntar czf "$backup" ./logs\n```\n\n'
    "改动点：先打包 ./logs，再删当前目录下的 *.log。\n"
)


@pytest.fixture
def chat(qtbot, restore_app):
    from tu_shell_agent.ui.theme import apply_theme

    apply_theme(restore_app)
    panel = ChatPanel()
    qtbot.addWidget(panel)
    panel.resize(430, 640)
    panel.show()
    return panel


def _turns(panel) -> list[QWidget]:
    return panel.transcript.findChildren(QWidget, "chatTurn")


def test_each_round_is_its_own_card(qtbot, chat):
    """两轮对话 → 两张卡片；每张卡片里有自己的用户块与回复标题。

    旧版是一个纯文本控件里拼出来的（`你：…` / `模型：…`），所有轮次连成一片 ——
    用户的原话是"每一轮会话都在一块，看不清楚"。
    """
    chat.add_user("第一轮的问题")
    chat.begin_stream("模型回复")
    chat.append_delta("第一轮的回答")
    chat.end_stream()
    chat.add_user("第二轮的问题")
    chat.begin_stream("模型回复")
    chat.append_delta("第二轮的回答")
    chat.end_stream()

    turns = _turns(chat)
    assert len(turns) == 2, f"两轮对话应当有两张卡片，实际 {len(turns)} 张"

    first_user = turns[0].findChild(QLabel, "chatUserText")
    second_user = turns[1].findChild(QLabel, "chatUserText")
    assert first_user is not None and "第一轮的问题" in first_user.text()
    assert second_user is not None and "第二轮的问题" in second_user.text(), (
        "第二条用户消息跑到别的卡片里去了（轮次没有分开）"
    )
    assert "第一轮的回答" not in second_user.text()


def test_the_reply_is_split_into_prose_and_a_code_block(qtbot, chat):
    """回复里的围栏代码块要**单独成块**（等宽 + 自己的复制按钮），散文留在正文块里。"""
    chat.add_user("怎么改")
    chat.begin_stream("模型回复")
    chat.append_delta(REPLY)
    chat.end_stream()

    turn = _turns(chat)[0]
    code = turn.findChild(QWidget, "chatCodeText")
    assert code is not None, "代码块没有单独成块"
    assert 'tar czf "$backup" ./logs' in code.toPlainText()
    assert "改动点" not in code.toPlainText(), "散文被塞进了代码块"

    bodies = [label.text() for label in turn.findChildren(QLabel, "chatReplyText")]
    assert any("没有先备份" in text for text in bodies)
    assert any("改动点" in text for text in bodies), "代码块之后的散文丢了"
    assert not any("tar czf" in text for text in bodies), "代码块又被抄进了散文块"


def test_code_block_wraps_instead_of_clipping(qtbot, chat):
    """代码块**必须能断行**：shell 脚本里一堆没有空格的长串，断不开就只能被裁掉半个。

    实测第一版用 `QLabel`：`backup="/tmp/logs-$(date +%s).tar.gz"` 在卡片里只看到一半
    （用户看到的是"脚本缺了一半"）。这里按"控件宽度 >= 最长那一行的自然宽度 或 允许按字符断行"
    来断言 —— 也就是：内容要么放得下，要么能断，不许被裁。
    """
    from PySide6.QtGui import QTextOption

    chat.add_user("写个备份脚本")
    chat.begin_stream("模型回复")
    chat.append_delta('```bash\nbackup="/tmp/logs-$(date +%s).tar.gz"\n```\n')
    chat.end_stream()

    code = _turns(chat)[0].findChild(QWidget, "chatCodeText")
    assert code is not None
    assert code.wordWrapMode() == QTextOption.WrapMode.WrapAtWordBoundaryOrAnywhere, (
        "代码块没有开启「按字符断行」：没有空格的长串会被裁掉"
    )
    assert code.horizontalScrollBarPolicy().name.endswith("AlwaysOff"), (
        "代码块出现了横向滚动条 —— 卡片里再嵌一层滚动条，滚轮该滚谁就说不清了"
    )


def test_code_block_height_follows_its_content(qtbot, chat):
    """代码块高度跟着内容走：不留一大片空白，也不把最后一行截掉。"""
    chat.add_user("写脚本")
    chat.begin_stream("模型回复")
    chat.append_delta("```bash\necho one\necho two\necho three\n```\n")
    chat.end_stream()
    qtbot.wait(30)          # 高度在布局排完之后才定下来（真实使用里有事件循环）

    code = _turns(chat)[0].findChild(QWidget, "chatCodeText")
    assert code is not None
    one_line = code.fontMetrics().height()
    assert one_line * 3 - 6 <= code.height() <= one_line * 5 + 12, (
        f"代码块高 {code.height()}px，三行内容（行高 {one_line}px）不该是这个高度"
    )


def test_activity_rows_carry_their_own_tag(qtbot, chat):
    """工具/技能这些"活动行"要带自己的小标签（读取 / 技能 / 提示），别一律写"提示"。"""
    chat.add_note("读取 read_file（plan.md）：把 .log 清掉")
    chat.add_note("技能已加载：shell-strict")
    chat.add_note("已拒绝这次修改")

    tags = [label.text() for label in chat.findChildren(QLabel, "chatActivityTag")]
    assert tags == ["读取", "技能", "提示"], tags


def test_error_is_its_own_block(qtbot, chat):
    """错误要独立成块（红框），不能混进正文 —— 混进去会被读成模型说的话。"""
    chat.add_user("在吗")
    chat.add_error("对话失败：模型 API 返回 HTTP 404")

    block = _turns(chat)[0].findChild(QWidget, "chatErrorBlock")
    assert block is not None, "错误没有独立成块"
    assert "404" in block.findChild(QLabel, "chatErrorText").text()


def test_copy_button_copies_the_whole_round(qtbot, chat):
    """卡片右下角那个复制按钮：复制的是**这一轮的全文**（用户+回复+代码）。"""
    chat.add_user("怎么改")
    chat.begin_stream("模型回复")
    chat.append_delta(REPLY)
    chat.end_stream()

    turn = _turns(chat)[0]
    footer = turn.findChild(QWidget, "chatTurnFooter")
    assert footer is not None
    button = footer.findChild(QWidget, "chatCopyButton")
    assert button is not None, "卡片页脚里没有复制按钮"
    button.click()

    copied = QApplication.clipboard().text()
    assert "怎么改" in copied and "没有先备份" in copied
    assert 'tar czf "$backup" ./logs' in copied, "复制的全文里丢了代码块"
    assert button.text() == "✓", "点完没有给出反馈"

    # 代码块自己的复制按钮只复制**那段代码**（不要用户消息、不要散文）
    code_button = turn.findChild(QWidget, "chatCodeBlock").findChild(QWidget, "chatCopyButton")
    assert code_button is not None
    code_button.click()
    only_code = QApplication.clipboard().text()
    assert only_code.startswith("#!/usr/bin/env bash") and "怎么改" not in only_code


def test_plain_text_contract_still_works_for_script_extraction(qtbot, chat):
    """纯文本契约没变：`transcript_text()` 仍能被 `extract_last_script` 抠出脚本。

    这条路是"把最新脚本放进中栏"的入口（引擎那侧只看纯文本），呈现方式换了它也不能断。
    """
    chat.add_user("给我脚本")
    chat.begin_stream("模型回复")
    chat.append_delta(REPLY)
    chat.end_stream()

    text = chat.transcript_text()
    assert "你：给我脚本" in text and "模型：" in text
    script = extract_last_script(text)
    assert script is not None and "tar czf" in script
    assert script.startswith("#!/usr/bin/env bash")


def test_history_loads_as_rounds(qtbot, chat):
    """从磁盘回填的历史也按轮次摆：一条 user 开一轮，模型回复挂进这一轮。"""
    class _Entry:
        def __init__(self, role, text, when=""):
            self.role, self.text, self.when = role, text, when

    chat.load_history(
        [
            _Entry("user", "第一问", "2026-09-20 20:41"),
            _Entry("model", "第一答", "2026-09-20 20:41"),
            _Entry("user", "第二问", "2026-09-20 20:42"),
            _Entry("error", "对话失败：HTTP 404", "2026-09-20 20:42"),
        ]
    )

    turns = _turns(chat)
    assert len(turns) == 2, f"回填后应当是两轮，实际 {len(turns)} 轮"
    assert "第一问" in turns[0].findChild(QLabel, "chatUserText").text()
    assert "第二问" in turns[1].findChild(QLabel, "chatUserText").text()
    assert turns[1].findChild(QWidget, "chatErrorBlock") is not None
    assert chat.transcript.placeholderText() and not chat.transcript._placeholder.isVisible(), (
        "有内容之后空记录区的说明该收起来"
    )


def test_split_segments_keeps_an_unfinished_fence(qtbot):
    """流式输出打到一半（围栏还没闭合）也要认成代码块，不能等到闭合才显示成代码。"""
    segments = split_segments("先这样：\n\n```bash\necho hi\n")
    assert ("code", "echo hi") in segments, segments
    assert segments[0][0] == "text"


# ── 输入卡片：模式胶囊与「＋」菜单 ─────────────────────────────────────


def test_mode_pill_shows_and_switches_the_backend(qtbot, chat):
    """胶囊显示当前后端的**短名**，点开能切换并发出信号。"""
    from tu_shell_agent.agent_backends import backend_descriptor

    chat.set_backend("builtin")
    assert "内置 agent" in chat.mode_button.text()
    assert "（" not in chat.mode_button.text(), "胶囊上只放短名（长了会被裁字）"

    chosen: list[str] = []
    chat.backend_changed.connect(chosen.append)
    actions = {action.text().lstrip("✓ "): action for action in chat.mode_menu.actions() if action.text()}
    assert "内置 agent（直连模型 API）" in actions, "菜单里没有当前后端"
    actions["opencode"].trigger()

    assert chosen == ["opencode"], f"切换后端没有发信号：{chosen}"
    assert "opencode" in chat.mode_button.text()
    assert backend_descriptor("opencode").display_name in chat.mode_button.toolTip()


def test_plus_menu_holds_the_secondary_actions(qtbot, chat):
    """「＋」菜单：存入中栏 / 扫描历史会话 / 新对话（都发得出信号）。"""
    texts = [action.text() for action in chat.plus_menu.actions() if action.text()]
    assert texts == ["把最新脚本放进中栏", "扫描历史会话", "新对话"], texts

    asked: list[str] = []
    chat.sessions_refresh_requested.connect(lambda: asked.append("scan"))
    chat.new_session_requested.connect(lambda: asked.append("new"))
    chat.plus_menu.actions()[-2].trigger()
    chat.plus_menu.actions()[-1].trigger()
    assert asked == ["scan", "new"]


def test_extract_action_puts_the_latest_script_out(qtbot, chat):
    """「把最新脚本放进中栏」在 ＋ 菜单里，取的是回复里最后一段脚本。"""
    chat.add_user("给我脚本")
    chat.begin_stream("模型回复")
    chat.append_delta(REPLY)
    chat.end_stream()

    got: list[str] = []
    chat.script_extracted.connect(got.append)
    chat.extract_action.trigger()

    assert got and "tar czf" in got[0]
    assert json.dumps(got[0]) is not None      # 进信号的是纯文本（不是控件对象）


def test_cancel_button_tracks_busy_state(qtbot, chat):
    """停止按钮只在忙时出现；输入框在忙时只读。"""
    assert not chat.cancel_button.isVisible()
    chat.set_busy(True)
    assert chat.cancel_button.isVisible() and chat.cancel_button.isEnabled()
    assert chat.input.isReadOnly(), "生成期间输入框应当只读"
    chat.set_busy(False)
    assert not chat.cancel_button.isVisible()
    assert not chat.input.isReadOnly()


def test_the_streaming_label_leaves_no_ghost(qtbot, chat):
    """流式标签定稿后**立刻**从屏幕上消失（不能等 `deleteLater`）。

    只把它移出布局是不够的：`deleteLater` 要等事件循环，在那之前它仍然是可见的散件，
    会按自己的旧几何画在卡片上 —— 卡片里出现一层"重影文字"，再叠上后面的散文，
    看上去像是排版错乱（实测抓图里非常明显）。
    """
    chat.add_user("问")
    chat.begin_stream("模型回复")
    chat.append_delta(REPLY)
    chat.end_stream()          # **刻意不等**事件循环：重影就是这一瞬间的事

    turn = _turns(chat)[0]
    # 判据是"还在不在卡片这棵树上"，**不能**用 isVisible()：刚建出来的控件在事件循环
    # 跑起来之前 isVisible() 就是 False，拿它当判据这条用例永远抓不到问题（实测踩到过）。
    leftovers = [
        label for label in turn.findChildren(QLabel, "chatReplyText") if "```bash" in label.text()
    ]
    assert not leftovers, (
        "流式标签还挂在卡片上（会按自己的旧几何画字，与分块正文叠成重影）"
    )
    assert chat._stream_label_ref() is None, "定稿后不该再留着流式标签的引用"
