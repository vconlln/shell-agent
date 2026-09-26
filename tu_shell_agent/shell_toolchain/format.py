"""shell 文本的规范化与格式化（换行符、行尾空白、行首缩进）。

**为什么放在 toolchain 而不是 ui**：这些都是"shell 文本"本身的性质，跟谁显示它无关 ——
界面里粘贴/编辑要用，引擎在送进 shellcheck 之前也要用（见 `run_controller.verify_edited`）。
放进 ui 会让引擎反向依赖界面。

**格式化的边界（重要，先读这一段）**：只动**行首空白**与**行尾空白**，行内的字符一个都不动。
shell 里行首空白几乎总是无意义的，只有两处例外，这两处必须原样放过：

1. **heredoc 正文**（`<<EOF … EOF`）：里面是数据，缩进是内容；
2. **跨行的引号字符串**（`echo "第一行\\n  第二行"`）：多行字符串里的空白也是内容。

除此之外重排缩进不改变脚本行为 —— 用例里有一条"原脚本与格式化后的脚本用 **bash 真跑一遍**，
stdout/stderr/退出码必须逐字相同"来钉住这条性质（比逐条断言缩进更接近用户真正在乎的东西）。

**缩进风格**（不是为了好看，是为了"看一眼就知道结构"）：

- 一档 4 个空格（Google Shell Style Guide 与 `ScriptView` 里 tab 的显示宽度一致）；
- `then` / `do` / `{` 之后进一档，`fi` / `done` / `}` 回到起点；
- `else` / `elif` 与自己的 `if` 对齐，之后的语句回到分支体那一档；
- `case … in` 之后：**分支标签进一档、分支体再进一档、`;;` 与分支体对齐**，
  `esac` 回到 `case` 那一档；
- 续行（上一行以 `\\` 结尾，或本行以 `|` / `&&` / `||` 开头）多缩一档；
- 函数体按 `{` / `}` 处理（因此函数体里的语句自然缩进一档）。

这不是 `shfmt` 的完整实现（那份实现上千行，还会重排换行与管道），刻意只做"空白"这一层：
改得越少，越不会有惊喜。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

# 缩进一档几个空格
INDENT = 4

# 收尾关键字：它们要与自己的开头对齐（打印时退一档，之后 level 也退一档）
_CLOSERS = frozenset({"fi", "done", "esac"})
# 分支关键字：与自己所属的开头对齐，但**之后的语句仍在分支体那一档**
_BRANCHES = frozenset({"else", "elif"})

# heredoc 起始：`<<EOF`、`<<-EOF`、`<<'EOF'`、`<<"EOF"`
_HEREDOC = re.compile(r"<<-?\s*(['\"]?)([A-Za-z_][A-Za-z0-9_]*)\1")
# case 分支标签：`a)`、`a|b)`、`*)`、`"x")`
_CASE_LABEL = re.compile(r"^(?:[^\s#()][^()]*|\([^()]*\))\s*\)\s*$")
# `case … in` 收尾（`in` 必须是一个词）
_CASE_OPEN = re.compile(r"(^|\s)case(\s|$)")
_WORD_END_IN = re.compile(r"(^|\s)in\s*$")


@dataclass(slots=True)
class FormatOutcome:
    """格式化结果：新文本 + 给用户看的改动说明（没人看得见的"自动"等于没做）。"""

    text: str
    notes: tuple[str, ...] = field(default_factory=tuple)

    @property
    def changed(self) -> bool:
        return bool(self.notes)


@dataclass(slots=True)
class _CaseFrame:
    """一层 `case` 的状态：标签该缩进到哪一档、当前是否在分支体里。"""

    base: int
    in_body: bool = False


def normalize_newlines(text: str) -> tuple[str, int]:
    """把 CRLF / CR 统一成 LF，返回 (新文本, 改掉了几处)。

    Windows 记事本、Git 的 `core.autocrlf`、从浏览器或聊天窗口复制的片段都会带 `\\r`。
    它混进脚本里最典型的症状是**看着一模一样的行**报 `$'\\r': command not found`，
    或者 heredoc 的结束符 `EOF\\r` 永远匹配不上、脚本一直读到文件尾。所以文本一进到我们手里
    就先归一 —— 用户不需要知道"换行符"这件事，但结果必须是他要的。
    """
    crlf = text.count("\r\n")
    lone_cr = text.count("\r") - crlf
    if not crlf and not lone_cr:
        return text, 0
    return text.replace("\r\n", "\n").replace("\r", "\n"), crlf + lone_cr


def split_code_comment(line: str) -> str:
    """返回这一行的**代码部分**（去掉行内注释）；引号里的 `#` 不算注释。

    `echo "#不是注释"` 与 `${x#prefix}` 都会被正确放过，否则"注释"会从字符串中间开始，
    缩进判定与语法高亮都会跟着错。
    """
    text = line.lstrip(" \t")
    quote = ""
    index = 0
    while index < len(text):
        char = text[index]
        if quote:
            if char == "\\" and quote == '"':
                index += 2
                continue
            if char == quote:
                quote = ""
            index += 1
            continue
        if char in "\"'":
            quote = char
            index += 1
            continue
        if char == "#" and (index == 0 or text[index - 1] in " \t"):
            return text[:index].rstrip()
        index += 1
    return text.rstrip()


def open_quote(line: str) -> str | None:
    """这一行结束时还有哪个引号没闭合（没有就返回 None）。"""
    quote = ""
    index = 0
    while index < len(line):
        char = line[index]
        if quote:
            if char == "\\" and quote == '"':
                index += 2
                continue
            if char == quote:
                quote = ""
        elif char in "\"'":
            quote = char
        index += 1
    return quote or None


def _first_word(code: str) -> str:
    parts = code.split()
    return parts[0].strip("();&|{}") if parts else ""


def format_script(text: str, *, indent: int = INDENT) -> FormatOutcome:
    """整理 shell 脚本的空白：换行符 → LF、去掉行尾空白、按结构重排行首缩进。

    只改空白（见模块说明的边界），因此**幂等**：格式化两次的结果与一次完全相同。
    """
    notes: list[str] = []
    normalized, fixed_newlines = normalize_newlines(text)
    if fixed_newlines:
        notes.append(f"换行符统一成 LF（{fixed_newlines} 处 CRLF/CR）")

    source = normalized.split("\n")
    while source and not source[-1].strip():      # 末尾空行先摘掉，最后统一补一个换行
        source.pop()

    out: list[str] = []
    level = 0
    continuation = False          # 上一行以 `\` 结尾 → 这一行是续行
    quote_open = False            # 跨行引号里：一个字都不许动
    heredoc: str | None = None    # 正在 heredoc 正文里
    heredoc_tabs = False
    cases: list[_CaseFrame] = []
    trailing_fixed = 0
    indent_fixed = 0
    original_ended_with_newline = bool(text) and text.endswith("\n")

    for raw in source:
        stripped = raw.strip()

        # ── heredoc 正文：原样保留（里面的缩进是数据）────────────────
        if heredoc is not None:
            body = raw.lstrip("\t") if heredoc_tabs else raw
            out.append(body.rstrip())
            if body.strip() == heredoc:
                heredoc = None
            continue

        if not stripped:
            out.append("")
            continue

        # `code` 只用来**判断结构**（首词、缩进档、是不是 case 标签），
        # 打印出去的永远是 `body`（去掉行首空白、去掉行尾空白的原文）——
        # 用 `code` 打印会把行内注释整段吃掉（实测：`echo hi # 说明` 变成 `echo hi`，
        # 用户写的注释就这么没了）。
        code = split_code_comment(raw)
        body = raw.lstrip(" \t").rstrip()
        if raw.rstrip() != raw:
            trailing_fixed += 1

        # ── 跨行引号里：保持原样 ────────────────────────────────────
        if quote_open:
            out.append(raw.rstrip())
            if open_quote(code) is None and not code.endswith("\\"):
                quote_open = False
            continue

        # ── 这一行缩进到哪一档 ─────────────────────────────────────
        word = _first_word(code)
        frame = cases[-1] if cases else None
        # 行首的 `}` / `)` 也是收尾（函数体、代码块、`$( … )`）：要与自己的开头对齐
        closer = word in _CLOSERS or code.startswith(("}", ")"))
        want = level
        if closer:
            want = frame.base if (word == "esac" and frame is not None) else max(level - 1, 0)
        elif word in _BRANCHES:
            want = max(level - 1, 0)
        elif frame is not None and code.startswith(";;"):
            want = frame.base + 2
        elif frame is not None and not frame.in_body and _CASE_LABEL.match(code):
            want = frame.base + 1
        elif frame is not None and not frame.in_body:
            want = frame.base + 1
        elif frame is not None:
            want = frame.base + 2
        # 续行只多**显示**一档，不进结构档：进了的话那个多出来的档会一直留在 level 上，
        # 后面所有行都跟着多缩一档（实测：`x=$( … )` 之后的函数定义被顶到了第二档）。
        extra = 1 if (continuation or code.startswith(("|", "&&", "||"))) else 0
        printed = max(want + extra, 0)

        new_line = " " * (printed * indent) + body
        if new_line != raw.rstrip():
            indent_fixed += 1
        out.append(new_line)

        # ── 状态推进 ───────────────────────────────────────────────
        # 1) case 内部：标签 → 分支体 → `;;` → 下一个标签
        if frame is not None:
            if code.startswith(";;"):
                frame.in_body = False
            elif not frame.in_body and _CASE_LABEL.match(code):
                frame.in_body = True
        # 2) 层数
        if closer:
            if word == "esac" and cases:
                cases.pop()
            level = want
        elif word in _BRANCHES:
            level = want + 1                  # 分支体回到"开头那一档 + 1"
        elif _CASE_OPEN.search(code) and _WORD_END_IN.search(code) and not code.startswith("#"):
            cases.append(_CaseFrame(base=want))
            level = want + 1
        elif code.endswith(("then", "do", "{", "(")) or re.search(r"\b(do|then)\s*$", code):
            level = want + 1
        elif code.endswith("}") and want > 0:
            level = want
        else:
            level = want
        # 3) 续行 / 跨行引号 / heredoc
        continuation = code.endswith("\\")
        # 引号没闭合与反斜杠续行是**两件事**：前者不管有没有反斜杠都要保持原样
        # （多行字符串里的空白是内容），只判反斜杠会让这种行被前面的语句当成一行处理。
        quote_open = open_quote(code) is not None
        match = _HEREDOC.search(code)
        if match:
            heredoc = match.group(2)
            heredoc_tabs = "<<-" in code

    if not out:
        # 空文本 / 全是空行：不编出一个换行来（空输入格式化后还是空）
        return FormatOutcome("", ())
    formatted = "\n".join(line.rstrip() for line in out) + "\n"
    if trailing_fixed:
        notes.append(f"去掉行尾空白（{trailing_fixed} 行）")
    if indent_fixed:
        notes.append(f"整理缩进（{indent_fixed} 行）")
    if text and not original_ended_with_newline:
        notes.append("文件末尾补了换行")
    return FormatOutcome(formatted, tuple(notes))


def tidy_line(line: str, *, indent: int = INDENT) -> str:
    """只整理**一行**：去掉行尾空白、把行首 tab 换成空格。

    用在"敲回车"与"行内编辑"这类即时动作上：整篇重排会把光标和用户正在对齐的东西一起动掉，
    这个只碰行首与行尾，是能被接受的自动整理。
    """
    body = line.lstrip(" \t")
    leading = line[: len(line) - len(body)]
    spaces = leading.replace("\t", " " * indent)
    return spaces + body.rstrip()
