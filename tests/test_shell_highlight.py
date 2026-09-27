"""shell 语法高亮（`ui/widgets/shell_highlight.py`）。

用户要求："中间这栏的 shell 代码高亮渲染……像 vscode 那种的"。

断言读的是**真正画出来的东西**：`QSyntaxHighlighter.setFormat()` 把格式记在块的
`QTextLayout` 上（不是文档字符格式），所以这里读 `block.layout().formats()` 的
（起始、长度、颜色）三元组 —— 这比"调用了某个方法"更接近用户看到的像素。
"""

from __future__ import annotations

import pytest
from PySide6.QtWidgets import QApplication, QPlainTextEdit

from tu_shell_agent.ui.widgets.shell_highlight import ShellHighlighter

SCRIPT = """#!/usr/bin/env bash
set -euo pipefail

# 整行注释
if [ -f "$plan" ]; then          # 行内注释
    echo "找到方案：${plan}"
fi

backup() {
    local dir="$1"
    tar czf "$dir.tar.gz" "$dir"
}

cat <<EOF
  heredoc 正文里的 $x 与 # 都按字符串上色
EOF

n=42
case "$n" in
    43) echo ok ;;
esac
"""


class _View:
    """给高亮器用的编辑器包装：**整个会话只用一个 QPlainTextEdit**。

    为什么不每个用例建一个：这些控件都是"没有父控件的顶层控件"，一个用例建一个的话，
    销毁时机就散落在各个用例的收尾里 —— 实测那种"控件寿命不受控"的组合会让整套用例
    在某次 Qt 全局重抛光（`apply_theme` → `setStyle`）里随机段错误
    （Python 栈落在别的用例甚至夹具收尾、C 栈落在 libQt6Widgets）。
    整轮共用一个、只在用例之间换文本，就不存在这个问题了。
    """

    _editor = None
    _highlighter = None

    def __init__(self, text: str) -> None:
        editor = _View._editor
        if editor is None:
            editor = QPlainTextEdit()
            editor.resize(600, 400)
            _View._editor = editor
            _View._highlighter = ShellHighlighter(editor.document())
        self.editor = editor
        self.highlighter = _View._highlighter
        self.highlighter.set_enabled(True)
        editor.setPlainText(text)
        QApplication.processEvents()

    def color(self, line_no: int, word: str) -> str:
        """第 line_no 行（1 起）里 `word` 首次出现处的颜色；没上色返回 "none"。"""
        block = self.editor.document().findBlockByNumber(line_no - 1)
        text = block.text()
        index = text.find(word)
        assert index >= 0, f"第 {line_no} 行没有 {word!r}：{text!r}"
        layout = block.layout()
        for fmt in layout.formats() if layout else []:
            if fmt.start <= index < fmt.start + fmt.length:
                return fmt.format.foreground().color().name()
        return "none"

    def colored_spans(self, line_no: int) -> list[tuple[str, str]]:
        block = self.editor.document().findBlockByNumber(line_no - 1)
        layout = block.layout()
        return [
            (block.text()[fmt.start : fmt.start + fmt.length], fmt.format.foreground().color().name())
            for fmt in (layout.formats() if layout else [])
        ]


@pytest.fixture(scope="module", autouse=True)
def _close_shared_editor():
    """整轮用完把共用的编辑器拆掉（不让它挂到会话结束）。"""
    yield
    editor = _View._editor
    if editor is not None:
        editor.close()
        editor.deleteLater()
        QApplication.processEvents()
    _View._editor = None
    _View._highlighter = None


@pytest.fixture
def view(qapp) -> _View:
    """`qapp` 是必需的：pytest-qt 只有在某个用例/夹具要它时才会创建 QApplication，
    而这个文件里的控件都不再交给 qtbot 托管 —— 少了它，第一次建控件就会
    "QWidget: Cannot create a QWidget without QApplication" 直接 abort。"""
    return _View(SCRIPT)


def test_shebang_has_its_own_color(view):
    assert view.color(1, "#!") not in ("none", "#000000")


def test_keywords_and_commands_have_different_colors(view):
    """`if` 是语法、`echo` 是命令 —— 两者必须一眼分得开（这正是"像 vscode"的意思）。"""
    keyword = view.color(5, "if")
    command = view.color(6, "echo")
    assert keyword != command, f"关键字与命令同色：{keyword}"
    assert keyword != "none" and command != "none"


def test_variables_strings_and_comments_are_colored(view):
    variable = view.color(6, "${plan}")
    string = view.color(6, '"')
    comment = view.color(4, "#")
    assert len({variable, string, comment}) == 3, (variable, string, comment)
    assert "none" not in (variable, string, comment)


def test_hash_inside_a_string_is_not_a_comment(qapp):
    """`echo "#不是注释"` 里的 `#` 必须按字符串上色。

    高亮与格式化共用同一套注释判定（`format.split_code_comment`），所以这条同时守住
    "高亮说是注释、格式化却按代码处理"那种自相矛盾。
    """
    view = _View('echo "# 不是注释"   # 这才是注释\n')
    string_color = view.color(1, '"')
    comment_color = view.color(1, "# 这才是注释")
    assert string_color != comment_color
    assert view.color(1, "# 不是注释") == string_color, "字符串里的 # 被当成注释了"


def test_function_name_and_builtins_are_colored(view):
    assert view.color(9, "backup") not in ("none", "#000000")
    assert view.color(10, "local") not in ("none", "#000000")
    assert view.color(10, "$1") not in ("none", "#000000")


def test_options_and_test_operators_are_distinguished(qapp):
    view = _View('if [ -z "$x" ]; then\n    ls -la --color=auto\nfi\n')
    assert view.color(1, "-z") != view.color(2, "-la"), "测试操作符与命令行选项同色"
    assert view.color(2, "-la") != "none"


def test_numbers_and_arithmetic(qapp):
    view = _View("n=42\nn=$((n + 7))\n")
    assert view.color(1, "42") not in ("none", "#000000")


def test_heredoc_body_is_string_colored_until_the_marker(view):
    """heredoc 正文（第 15 行）按字符串上色，结束标记 `EOF`（第 16 行）换一种色。"""
    body = view.color(15, "heredoc")
    marker = view.color(16, "EOF")
    assert body not in ("none", "#000000")
    assert marker not in ("none", "#000000")
    assert body != marker


def test_a_closed_string_does_not_leak_into_the_next_line(qapp):
    """成对的字符串之后，下一行必须是普通代码。

    这里曾经有个真 bug：字符串那一支无条件把 `quote` 留在"未闭合"上，于是
    `echo "hi"` 之后**所有**行都被当成字符串继续上色（`fi` 变成橙色、关键字全丢）。
    用"下一行的 `fi` 必须是关键字色"来钉住它。
    """
    script = '\n'.join(['echo "hi"', "if true; then", "    echo ok", "fi", "echo after", ""])
    view = _View(script)
    keyword = view.color(2, "if")
    string = view.color(1, '"')
    assert keyword != string, f"字符串状态漏到了下一行（if 用了字符串色 {keyword}）"
    assert view.color(5, "echo") != string, "闭合之后的普通行仍是字符串色"
    assert view.color(5, "echo") == view.color(1, "echo"), "命令色在后续行不一致"


def test_blank_line_inside_a_heredoc_does_not_end_it(qapp):
    """heredoc 正文里的**空行**不能把它判成结束（空行的 strip() 与空标记同形）。"""
    view = _View("cat <<EOF\n第一行\n\n第三行\nEOF\necho after\n")
    body_color = view.color(2, "第一行")
    assert view.color(4, "第三行") == body_color, "空行之后 heredoc 就断了"
    assert view.color(5, "EOF") != body_color, "结束标记没有换色"
    assert view.color(6, "echo") != body_color, "结束后的普通行还按字符串上色"


def test_large_script_highlights_quickly(qapp):
    """2000 行的脚本高亮要够快（中栏是拿来读大脚本的，卡住就等于白做）。"""
    import time

    script = "\n".join(
        f'if [ -f "f{i}.log" ]; then echo "第 {i} 行：${{HOME}}/x" # 注释\nfi' for i in range(1000)
    )
    started = time.perf_counter()
    view = _View(script)
    elapsed = time.perf_counter() - started
    assert elapsed < 5.0, f"高亮 2000 行用了 {elapsed:.1f}s"
    assert view.color(1, "if") != "none"


def test_multiline_string_state_carries_to_the_next_line(qapp):
    """引号没闭合：下一行继续按字符串上色；闭合之后那一行不再有字符串色。"""
    view = _View('echo "第一行\n第二行 里的 $x\n结束"\necho after\n')
    string_color = view.color(1, '"')

    second_line = [color for _text, color in view.colored_spans(2)]
    assert string_color in second_line, f"跨行字符串的第二行没有继续上色：{view.colored_spans(2)}"
    assert view.color(2, "$x") != string_color, "双引号里的变量应当按变量上色"

    fourth_line = [color for _text, color in view.colored_spans(4)]
    assert string_color not in fourth_line, "字符串状态漏到了闭合之后的下一行"


def test_highlighting_can_be_turned_off(qapp):
    """设置里允许关掉高亮：关掉之后任何行都不该有色段。"""
    view = _View("if true; then\n    echo hi\nfi\n")
    assert view.colored_spans(1), "默认应当有高亮"
    view.highlighter.set_enabled(False)
    QApplication.processEvents()
    assert view.colored_spans(1) == [], "关掉之后还在上色"
    view.highlighter.set_enabled(True)
    QApplication.processEvents()
    assert view.colored_spans(1), "重新打开后没有恢复"


def test_empty_document_is_safe(qapp):
    view = _View("")
    assert view.colored_spans(1) == []


def test_highlighting_does_not_change_the_text(view):
    """高亮只改格式，一个字符都不许动（脚本是要拿去执行的）。"""
    assert view.editor.toPlainText() == SCRIPT


def test_span_colors_stay_inside_their_own_token(qtbot):
    """颜色段不能越界：`echo` 的黄色不该染到它后面的空格与下一个词上。"""
    view = _View("echo hi\n")
    spans = dict(reversed(view.colored_spans(1)))
    assert spans.get("echo") is not None
    assert "echohi" not in "".join(spans), spans


def test_tab_indented_line_keeps_quotes_as_strings(qapp):
    """用 tab 缩进的那一行，收尾引号也是字符串色（不能因为行首空白被当成注释）。"""
    view = _View('\techo "缩进用 tab"\n')
    spans = view.colored_spans(1)
    string_color = view.color(1, '"')
    assert '"缩进用 tab"' in dict(spans) or any(
        text == '"缩进用 tab"' for text, _color in spans
    ), f"字符串被拆开了（收尾引号跑掉了）：{spans}"
    assert view.color(1, "echo") != string_color
