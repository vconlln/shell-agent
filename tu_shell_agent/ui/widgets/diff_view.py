"""两段脚本的逐行差异 → 中栏「对比上一轮」页用的 HTML。

纯字符串进出：不读文件、不起子进程、不引第三方 diff 库，difflib 足够。
"""

from __future__ import annotations

import difflib
from html import escape
from ..theme import DIFF_ADDED_BG, DIFF_ADDED_FG, DIFF_GUTTER_FG, DIFF_REMOVED_BG, DIFF_REMOVED_FG

# diff-added / diff-removed 是界面测试与后续样式表共用的契约类名，不要改名。
# 颜色写成行内样式而不是 <style> 里的类选择器：Qt 只支持 CSS 2.1 的一个子集，
# 实测行内 white-space:pre 能保住缩进，而只靠 class 时 Qt 会吞掉前导空格。
# 深色底上的 diff 配色：浅绿/浅粉底在 #181818 上是刺眼的色块，改用低饱和深底 + 亮前景
_STYLE_ADDED = f"background-color:{DIFF_ADDED_BG};color:{DIFF_ADDED_FG};white-space:pre;"
_STYLE_REMOVED = f"background-color:{DIFF_REMOVED_BG};color:{DIFF_REMOVED_FG};white-space:pre;"
_STYLE_EQUAL = "white-space:pre;"  # 上下文行不加底色，跟随主题颜色
_STYLE_GUTTER = f"color:{DIFF_GUTTER_FG};"
_STYLE_SUMMARY = "margin-bottom:6px;"

_BLANK_NUMBER = "    "  # 无行号的列（新增没有旧行号）用空格撑住，否则两列会串位


def render_diff_html(old: str, new: str) -> str:
    """把「上一轮 → 本轮」渲染成 HTML；old 为空表示整段都是新增。

    splitlines() 而不是 split("\n")：脚本末尾的换行不该算成一个空行，
    否则每轮对比都会多出一条假差异。
    """
    old_lines = old.splitlines()
    new_lines = new.splitlines()
    # autojunk 对大脚本会丢掉「高频行」导致 diff 变糊，逐行对比不需要这个启发式
    matcher = difflib.SequenceMatcher(a=old_lines, b=new_lines, autojunk=False)

    rows: list[str] = []
    added = 0
    removed = 0
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            for offset, line in enumerate(old_lines[i1:i2]):
                rows.append(
                    _row("diff-equal", _STYLE_EQUAL, " ", line, i1 + offset + 1, j1 + offset + 1)
                )
            continue
        if tag in ("replace", "delete"):
            for offset, line in enumerate(old_lines[i1:i2]):
                rows.append(_row("diff-removed", _STYLE_REMOVED, "-", line, i1 + offset + 1, None))
            removed += i2 - i1
        if tag in ("replace", "insert"):
            for offset, line in enumerate(new_lines[j1:j2]):
                rows.append(_row("diff-added", _STYLE_ADDED, "+", line, None, j1 + offset + 1))
            added += j2 - j1

    if added or removed:
        summary = f"新增 {added} 行，删除 {removed} 行。"
    else:
        summary = "两轮脚本一致，没有差异。"
    header = f'<div class="diff-summary" style="{_STYLE_SUMMARY}">{summary}</div>'
    return header + "".join(rows)


def diff_counts(old: str, new: str) -> tuple[int, int]:
    """(新增行数, 删除行数) —— 与 `render_diff_html` 用**同一套**判定。

    单独抽出来是因为"提议栏"要显示 +N −M，而它不该去解析自己刚生成的 HTML。
    """
    old_lines = old.splitlines()
    new_lines = new.splitlines()
    matcher = difflib.SequenceMatcher(a=old_lines, b=new_lines, autojunk=False)
    added = removed = 0
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag in ("replace", "delete"):
            removed += i2 - i1
        if tag in ("replace", "insert"):
            added += j2 - j1
    return added, removed


def _row(
    css_class: str, style: str, marker: str, text: str, old_no: int | None, new_no: int | None
) -> str:
    """一行 diff：左边两列行号，右边 +/- 标记与正文（正文转义后原样呈现）。"""
    gutter = f"{_num(old_no)} {_num(new_no)} │ "
    return (
        f'<div class="{css_class}" style="{style}">'
        f'<span class="diff-lineno" style="{_STYLE_GUTTER}">{gutter}</span>'
        f"{marker} {escape(text, quote=False)}</div>"
    )


def _num(value: int | None) -> str:
    return _BLANK_NUMBER if value is None else f"{value:>4}"
