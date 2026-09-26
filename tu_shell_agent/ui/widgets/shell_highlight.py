"""shell 脚本的语法高亮（中栏「本轮」页与确认框那两处脚本视图）。

用户要求："中间这栏的 shell 代码高亮渲染，这样看上去不会很枯燥，像 vscode 那种的"。

实现用 `QSyntaxHighlighter`（Qt 自带的那套）：它按块（行）回调，只重画变化的行，
所以滚动大脚本也不卡。配色取 VS Code Dark+ 的色相 —— 用户点名要"像 vscode 那种"，
但在我们这套深色底上把饱和度收了一档（直接用原色在这块冷灰底上会显得发荧光）。

**跨行状态**（这是高亮器最容易写错的地方，也是它必须按行记账的原因）：

- heredoc 正文（`<<EOF … EOF`）：整段按"字符串"上色，直到结束标记那一行；
- 未闭合的引号：下一行继续是字符串；
- 行内注释的 `#` 不能认错（`echo "#不是注释"`、`${x#pre}`）。

三件事都用 `setCurrentBlockState` 把状态传给下一行 —— 与格式化那一层用的是同一套判断
（`format.split_code_comment` / `format.open_quote`），不各写一份，免得两处对"什么算注释"
的理解漂移（那样会出现"高亮说是注释、格式化却按代码处理"这种自相矛盾）。
"""

from __future__ import annotations

from PySide6.QtCore import QRegularExpression
from PySide6.QtGui import QColor, QFont, QSyntaxHighlighter, QTextCharFormat

from ...shell_toolchain.format import open_quote, split_code_comment

# 块状态：0 = 普通代码，1 = heredoc 正文，2/3 = 跨行字符串（双引号 / 单引号）
# 两种引号分开记：双引号里 `$var` 会展开（要继续按变量上色），单引号里不会。
STATE_NORMAL = 0
STATE_HEREDOC = 1
STATE_STRING = 2
STATE_STRING_SINGLE = 3

# 控制关键字（`if then else fi` 这一套）
KEYWORDS = (
    "if", "then", "else", "elif", "fi", "for", "while", "until", "do", "done",
    "case", "esac", "in", "function", "select", "time", "coproc", "return",
    "break", "continue", "exit", "shift", "local", "declare", "readonly",
    "export", "unset", "eval", "exec", "trap", "source", "set",
)

# 常用命令/内置命令（与关键字分开上色：读脚本时"这是命令还是语法"要一眼分得开）
COMMANDS = (
    "echo", "printf", "read", "cd", "pwd", "ls", "cp", "mv", "rm", "mkdir",
    "rmdir", "touch", "cat", "head", "tail", "wc", "sort", "uniq", "grep",
    "sed", "awk", "cut", "tr", "find", "xargs", "tee", "tar", "gzip", "gunzip",
    "chmod", "chown", "ln", "df", "du", "ps", "kill", "sleep", "date", "test",
    "true", "false", "command", "type", "which", "env", "basename", "dirname",
    "realpath", "mktemp", "seq", "yes", "diff", "sha256sum", "curl", "wget",
    "git", "docker", "kubectl", "python3", "pip", "npm", "node", "make",
    "shellcheck", "systemctl", "journalctl",
)

# 测试/条件里的操作符（`[[ -z "$x" ]]`、`[ "$a" == "$b" ]`）
TEST_OPERATORS = (
    "-z", "-n", "-e", "-f", "-d", "-r", "-w", "-x", "-s", "-L", "-h",
    "-eq", "-ne", "-lt", "-le", "-gt", "-ge", "-nt", "-ot",
)


def _palette() -> dict[str, QTextCharFormat]:
    """各语法成分的字符格式。颜色写在这里（而不是主题 QSS 里）：`QSyntaxHighlighter`
    用的是字符格式，QSS 管不到它 —— 想改配色改这一处就够。"""
    def fmt(color: str, *, bold: bool = False, italic: bool = False) -> QTextCharFormat:
        style = QTextCharFormat()
        style.setForeground(QColor(color))
        if bold:
            style.setFontWeight(QFont.Weight.DemiBold)
        if italic:
            style.setFontItalic(True)
        return style

    return {
        "comment": fmt("#7f9a6a", italic=True),     # VS Code #6A9955 收了一档饱和
        "shebang": fmt("#9ec27a", italic=True),
        "keyword": fmt("#c586c0"),                  # 控制关键字：紫
        "command": fmt("#dcdcaa"),                  # 命令/内置：黄
        "string": fmt("#ce9178"),                   # 字符串：橙
        "variable": fmt("#9cdcfe"),                 # 变量：浅蓝
        "number": fmt("#b5cea8"),
        "operator": fmt("#d4d4d4"),
        "option": fmt("#c8c8c8"),                   # -x / --long
        "test": fmt("#4ec9b0"),                     # [[ -z ]] 之类的测试操作符
        "heredoc": fmt("#ce9178"),                  # heredoc 正文＝字符串色
        "heredoc_mark": fmt("#c586c0", bold=True),  # <<EOF 与结束的那行 EOF
        "function": fmt("#dcdcaa"),                 # `name()` 里的函数名
    }


class ShellHighlighter(QSyntaxHighlighter):
    """shell 语法高亮：按行记账（heredoc、跨行字符串都由块状态带到下一行）。"""

    def __init__(self, document, *, enabled: bool = True) -> None:
        super().__init__(document)
        self._styles = _palette()
        self._enabled = enabled

    def set_enabled(self, enabled: bool) -> None:
        """关掉高亮（设置里可以关：有人就是喜欢纯色文本）。关掉后重新解析一遍。"""
        self._enabled = bool(enabled)
        self.rehighlight()

    def enabled(self) -> bool:
        return self._enabled

    # ── 主流程 ────────────────────────────────────────────────────────
    def highlightBlock(self, text: str) -> None:  # noqa: N802 - Qt 命名
        if not self._enabled:
            self.setCurrentBlockState(STATE_NORMAL)
            return

        previous = self.previousBlockState()
        if previous == STATE_HEREDOC:
            # heredoc 正文：整行按字符串上色，直到结束标记（结束标记自己换一种色）
            marker = getattr(self, "_heredoc_marker", "")
            # `marker` 为空时**绝不能**判成"结束行"：空行的 strip() 也是空串，
            # 那会让 heredoc 在第一行空行处提前结束（后面的数据全被当代码上色）。
            if marker and text.strip() == marker:
                self.setFormat(0, len(text), self._styles["heredoc_mark"])
                self.setCurrentBlockState(STATE_NORMAL)
                self._heredoc_marker = ""
                return
            self.setFormat(0, len(text), self._styles["heredoc"])
            self.setCurrentBlockState(STATE_HEREDOC)
            return

        code = split_code_comment(text)
        quote_carry = {STATE_STRING: '"', STATE_STRING_SINGLE: "'"}.get(previous, "")

        # 1) 注释（整行注释与行内注释）。跨行字符串里的一行不算注释：
        #    以 `#` 开头的行可能是字符串的第二行，按注释上色就错了。
        comment_at = None if quote_carry else self._comment_index(text)
        limit = len(text) if comment_at is None else comment_at
        if comment_at is not None:
            self.setFormat(comment_at, len(text) - comment_at, self._styles["comment"])

        # 2) 第一行 `#!` 单独一种色
        if text.startswith("#!"):
            self.setFormat(0, len(text), self._styles["shebang"])
            self.setCurrentBlockState(STATE_NORMAL)
            return

        # 3) heredoc 起点：`<<EOF`（正文从下一行开始）
        match = self._heredoc_start(text, limit)
        if match is not None:
            start, end, marker = match
            self.setFormat(start, end - start, self._styles["heredoc_mark"])
            self._heredoc_marker = marker
            self._highlight_range(text, 0, start)
            # 块状态标成 HEREDOC：**下一行**才是正文（正文那一支靠 previousBlockState 认出来）。
            # 标成 NORMAL 的话正文那一支永远进不去 —— 实测 heredoc 里的内容一点颜色都没有。
            self.setCurrentBlockState(STATE_HEREDOC)
            return

        # 4) 普通代码：字符串 / 变量 / 数字 / 关键字 / 命令 / 选项
        #    `quote_carry` 是上一行没闭合的引号：这一行从"字符串中间"开始扫
        trailing = self._highlight_range(text, 0, limit, quote_carry=quote_carry)
        # 没闭合的引号 → 下一行接着当字符串（双引号与单引号分开记）
        # 用**扫描结果**定状态：`open_quote()` 假设这一行从普通状态开始，
        # 而跨行字符串的结束行恰恰不是 —— 它会把那个**收尾**的引号当成新的开引号，
        # 于是字符串状态一直漏到后面所有行（实测：第 4 行整行被涂成字符串色）。
        unclosed = trailing
        if unclosed == '"':
            self.setCurrentBlockState(STATE_STRING)
        elif unclosed == "'":
            self.setCurrentBlockState(STATE_STRING_SINGLE)
        else:
            self.setCurrentBlockState(STATE_NORMAL)

    # ── 内部的词法处理 ────────────────────────────────────────────────
    def _comment_index(self, text: str) -> int | None:
        """行内注释起点（引号里的 `#` 不算）；没有注释返回 None。"""
        if not text.strip().startswith("#"):
            code = split_code_comment(text)
            if code == text.rstrip():
                return None
            # `split_code_comment` 只给出代码部分，长度差就是注释起点（去掉尾空白后的）
            index = len(code)
            while index < len(text) and text[index] in " \t":
                index += 1
            return index if index < len(text) else None
        stripped = text.lstrip(" \t")
        return len(text) - len(stripped)

    def _heredoc_start(self, text: str, limit: int) -> tuple[int, int, str] | None:
        match = QRegularExpression(r"<<-?\s*(['\"]?)([A-Za-z_][A-Za-z0-9_]*)\1").match(
            text[:limit]
        )
        if not match.hasMatch():
            return None
        return match.capturedStart(0), match.capturedEnd(0), match.captured(2)

    def _highlight_range(
        self, text: str, start: int, limit: int, *, quote_carry: str = ""
    ) -> str:
        """给 [start, limit) 里的代码上色；`quote_carry` 是上一行没闭合的引号。

        返回**这一行结束时**还没闭合的引号（""/"'"/'"'）—— 调用方用它决定块状态。
        """
        styles = self._styles
        index = start
        quote = quote_carry
        if quote:
            self.setFormat(start, limit - start, styles["string"])
        while index < limit:
            char = text[index]

            # 字符串（含转义）；双引号里 `$var` 要展开，所以继续按变量上色
            if quote:
                if char == "\\" and quote == '"':
                    index += 2
                    continue
                if char == "$" and quote == '"':
                    end = self._variable_end(text, index, limit)
                    if end > index + 1:
                        self.setFormat(index, end - index, styles["variable"])
                        index = end
                        continue
                if char == quote:
                    quote = ""
                index += 1
                continue
            if char in "\"'":
                end, closed = self._string_end(text, index, limit)
                self.setFormat(index, end - index, styles["string"])
                # 字符串里的 `$变量` 仍然上变量色（双引号里会展开）
                if char == '"':
                    self._highlight_variables(text, index, end)
                # 只有**没闭合**的引号才留给下一行。这里曾经无条件 `quote = char`，
                # 于是任何一个成对的字符串（`echo "hi"`）都会把字符串状态漏给下一行 ——
                # 后面所有行都被涂成字符串色（实测：`fi` 变成了橙色）。
                if not closed:
                    quote = char
                index = end
                continue

            # 变量：$name / ${name} / $1 / $@ / $?
            if char == "$":
                end = self._variable_end(text, index, limit)
                if end > index + 1:
                    self.setFormat(index, end - index, styles["variable"])
                    index = end
                    continue

            # 数字
            if char.isdigit() and (index == start or not text[index - 1].isalnum()):
                end = index
                while end < limit and (text[end].isdigit() or text[end] == "."):
                    end += 1
                self.setFormat(index, end - index, styles["number"])
                index = end
                continue

            # 选项 -x / --long
            if char == "-" and (index == start or text[index - 1] in " \t=|&;("):
                end = index + 1
                while end < limit and (text[end].isalnum() or text[end] in "-_"):
                    end += 1
                if end > index + 1:
                    word = text[index:end]
                    style = styles["test"] if word in TEST_OPERATORS else styles["option"]
                    self.setFormat(index, end - index, style)
                    index = end
                    continue

            # 单词：关键字 / 命令 / 函数名
            if char.isalpha() or char == "_":
                end = index
                while end < limit and (text[end].isalnum() or text[end] in "_."):
                    end += 1
                word = text[index:end]
                if word in KEYWORDS:
                    self.setFormat(index, end - index, styles["keyword"])
                elif text[end:end + 1] == "(" and text[end + 1:end + 2] == ")":
                    self.setFormat(index, end - index, styles["function"])
                elif word in COMMANDS:
                    self.setFormat(index, end - index, styles["command"])
                index = end
                continue

            # 其余（管道、重定向、括号…）用操作符色，只是让它们不显得"没生效"
            if char in "|&;<>(){}[]":
                self.setFormat(index, 1, styles["operator"])
            index += 1
        return quote

    def _string_end(self, text: str, start: int, limit: int) -> tuple[int, bool]:
        """从 `start` 处的引号开始找结束位置，返回 (结束下标, 是否闭合)。

        **必须把"闭合没闭合"单独返回**：只回一个下标时，"未闭合"与"闭合引号正好是这一段的
        最后一个字符"是同一个值（都等于 limit），调用方没法区分 —— 早期版本就因此把
        `echo "hi"` 当成了未闭合，字符串状态一路漏到后面所有行。
        """
        quote = text[start]
        index = start + 1
        while index < limit:
            if text[index] == "\\" and quote == '"':
                index += 2
                continue
            if text[index] == quote:
                return index + 1, True
            index += 1
        return limit, False

    def _variable_end(self, text: str, start: int, limit: int) -> int:
        index = start + 1
        if index >= limit:
            return start
        if text[index] == "{":
            end = text.find("}", index)
            return limit if end < 0 else end + 1
        if text[index] in "@*#?$!-0123456789":
            return index + 1
        if text[index].isalpha() or text[index] == "_":
            end = index
            while end < limit and (text[end].isalnum() or text[end] == "_"):
                end += 1
            return end
        return start

    def _highlight_variables(self, text: str, start: int, end: int) -> None:
        index = text.find("$", start, end)
        while index >= 0:
            var_end = self._variable_end(text, index, end)
            if var_end > index + 1:
                self.setFormat(index, var_end - index, self._styles["variable"])
            index = text.find("$", max(var_end, index + 1), end)
