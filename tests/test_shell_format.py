"""shell 文本的规范化与格式化（`shell_toolchain/format.py`）。

用户要求："自动格式化代码，不论我是删除还是粘贴都自动识别换行符号等等"。

这里最要紧的一条**不是**"缩进好不好看"，而是**格式化不许改变脚本行为**：
`test_formatting_never_changes_what_the_script_does` 拿原脚本与格式化后的脚本**用 bash 真跑一遍**，
逐字比较 stdout / stderr / 退出码。缩进断言只是保证"看起来整齐"，语义断言保证"跑起来一样"。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tu_shell_agent.shell_toolchain.format import (
    format_script,
    normalize_newlines,
    split_code_comment,
    tidy_line,
)

MESSY = """#!/usr/bin/env bash
set -euo pipefail
if [ -f plan.md ]; then
echo "有方案"
else
echo "没有"
fi
for f in *.log; do
wc -l "$f"
done
"""

TIDY = """#!/usr/bin/env bash
set -euo pipefail
if [ -f plan.md ]; then
    echo "有方案"
else
    echo "没有"
fi
for f in *.log; do
    wc -l "$f"
done
"""


# ── 换行符：用户点名的"自动识别换行符号" ─────────────────────────────


def test_crlf_and_lone_cr_are_normalized():
    text, fixed = normalize_newlines("a\r\nb\rc\nd\r\n")
    assert text == "a\nb\nc\nd\n"
    assert fixed == 3, "两处 CRLF + 一处裸 CR"


def test_clean_text_is_left_alone():
    text, fixed = normalize_newlines("a\nb\n")
    assert (text, fixed) == ("a\nb\n", 0)


def test_pasting_crlf_gets_reported_and_fixed():
    """从 Windows 记事本/浏览器粘贴的脚本带 CRLF：要**认出来、改掉、并说一句**。

    不说的话用户只会看到脚本"莫名其妙不执行"（`set -euo pipefail\\r` 会报
    `$'\\r': command not found`，而行看起来一模一样）。
    """
    outcome = format_script("#!/bin/bash\r\nset -euo pipefail\r\necho hi\r\n")
    assert "\r" not in outcome.text
    assert any("CRLF" in note for note in outcome.notes), outcome.notes
    assert outcome.text == "#!/bin/bash\nset -euo pipefail\necho hi\n"


def test_trailing_whitespace_and_missing_final_newline():
    outcome = format_script("echo one   \necho two\t\n\necho three")
    assert outcome.text == "echo one\necho two\n\necho three\n"
    assert any("行尾空白" in note for note in outcome.notes)
    assert any("末尾" in note for note in outcome.notes)


# ── 缩进：结构要对上 ──────────────────────────────────────────────────


def test_if_else_fi_is_indented():
    assert format_script(MESSY).text == TIDY


def test_case_labels_bodies_and_double_semicolon():
    """`case` 的三层：标签、分支体、`;;`（`esac` 回到 `case` 那一档）。"""
    source = 'case "$1" in\na|b)\necho "ab"\n;;\n*)\necho other\n;;\nesac\necho done\n'
    expected = (
        'case "$1" in\n'
        "    a|b)\n"
        '        echo "ab"\n'
        "        ;;\n"
        "    *)\n"
        "        echo other\n"
        "        ;;\n"
        "esac\n"
        "echo done\n"
    )
    assert format_script(source).text == expected


def test_functions_and_nested_blocks():
    source = "backup() {\nlocal dir=\"$1\"\nif [ -d \"$dir\" ]; then\ntar czf x \"$dir\"\nfi\n}\necho after\n"
    expected = (
        "backup() {\n"
        '    local dir="$1"\n'
        '    if [ -d "$dir" ]; then\n'
        '        tar czf x "$dir"\n'
        "    fi\n"
        "}\n"
        "echo after\n"
    )
    assert format_script(source).text == expected


def test_continuations_and_command_substitution():
    source = "x=$(\nls\n| wc -l\n)\nlong --a \\\n--b\n"
    assert format_script(source).text == (
        "x=$(\n    ls\n        | wc -l\n)\nlong --a \\\n    --b\n"
    )


def test_comments_keep_their_level_and_inline_comments_are_kept():
    source = "if true; then\n# 注释跟着分支体\necho hi # 行内注释\nfi\n"
    assert format_script(source).text == (
        "if true; then\n    # 注释跟着分支体\n    echo hi # 行内注释\nfi\n"
    )


def test_hash_inside_a_string_is_not_a_comment():
    """`${x#prefix}` 与 `echo "#不是注释"` 里的 `#` 都不是注释起点。"""
    assert split_code_comment('echo "#不是注释"') == 'echo "#不是注释"'
    assert split_code_comment("v=${x#pre}   # 这个才是") == "v=${x#pre}"
    assert format_script('echo "${x#pre}" # 尾巴\n').text == 'echo "${x#pre}" # 尾巴\n'


# ── 两处"一个字都不许动"的地方 ────────────────────────────────────────


def test_heredoc_body_is_never_touched():
    """heredoc 正文是**数据**：里面的缩进必须原样保留（改了就是改了内容）。"""
    source = "cat <<EOF\n   第一行 有缩进\n第二行\nEOF\necho after\n"
    outcome = format_script(source)
    assert "   第一行 有缩进" in outcome.text, outcome.text
    assert outcome.text.endswith("EOF\necho after\n")


def test_heredoc_with_dashes_strips_tabs_only():
    """`<<-EOF` 允许（也只允许）用 tab 缩进正文 —— 保持这个语义。"""
    source = "cat <<-EOF\n\t内容\n\tEOF\necho after\n"
    assert format_script(source).text == "cat <<-EOF\n内容\nEOF\necho after\n"


def test_multiline_quoted_string_is_never_touched():
    """跨行字符串里的空白是**内容**：`echo "a\\n    b"` 的那个缩进不能被"整理"掉。"""
    source = 'echo "第一行\n    第二行 有缩进"\necho after\n'
    outcome = format_script(source)
    assert "    第二行 有缩进" in outcome.text, outcome.text


# ── 幂等 + 行为不变（这两条是这一层的底线）────────────────────────────


CASES = [
    MESSY,
    TIDY,
    'case "$1" in\na)\necho a\n;;\nesac\n',
    "f() {\nif true; then\necho x\nfi\n}\n",
    "cat <<EOF\n  数据\nEOF\n",
    'echo "多行\n   字符串"\n',
    "x=$(\nls\n)\n",
    "#!/usr/bin/env bash\n\n# 只有注释\n\n",
    "",
    "\n\n",
]


@pytest.mark.parametrize("source", CASES)
def test_formatting_is_idempotent(source):
    once = format_script(source).text
    twice = format_script(once).text
    assert once == twice, f"格式化两次结果不同：\n{once!r}\n{twice!r}"


SEMANTIC = {
    "条件与循环": (
        "#!/usr/bin/env bash\n"
        "set -euo pipefail\n"
        "if true; then\necho yes\nelse\necho no\nfi\n"
        "for i in 1 2 3; do\necho \"n=$i\"\ndone\n"
        "n=0\nwhile [ \"$n\" -lt 2 ]; do\nn=$((n+1))\necho \"w=$n\"\ndone\n"
    ),
    "case 与函数": (
        "#!/usr/bin/env bash\n"
        "set -euo pipefail\n"
        "greet() {\nlocal who=\"$1\"\ncase \"$who\" in\na)\necho hi-a\n;;\n*)\necho hi-other\n;;\nesac\n}\n"
        'greet a\ngreet z\n'
    ),
    "heredoc 与多行字符串": (
        "#!/usr/bin/env bash\n"
        "set -euo pipefail\n"
        "cat <<EOF\n   保留缩进的数据\nEOF\n"
        'printf "%s\\n" "第一行\n第二行"\n'
    ),
    "续行与命令替换": (
        "#!/usr/bin/env bash\n"
        "set -euo pipefail\n"
        "printf '%s|%s\\n' \\\none \\\ntwo\n"
        "echo \"count=$(printf 'a\\nb\\n' | wc -l)\"\n"
    ),
    "注释与行尾空白": (
        "#!/usr/bin/env bash\n"
        "set -euo pipefail\n"
        "echo one   # 行尾有空格   \n"
        "# 整行注释\n"
        "echo two\n"
    ),
}


@pytest.mark.parametrize("name", sorted(SEMANTIC))
def test_formatting_never_changes_what_the_script_does(name, tmp_path, bash_path):
    """格式化前后用 **bash 真跑一遍**，stdout / stderr / 退出码必须逐字相同。

    这是这一层最重要的性质：缩进只是排版，排错了不能把脚本排坏。
    """
    from tu_shell_agent.shell_toolchain.execute import run_script

    original = SEMANTIC[name]
    formatted = format_script(original).text
    assert formatted != "" and formatted.endswith("\n")

    results = []
    for label, script in (("原脚本", original), ("格式化后", formatted)):
        path = tmp_path / f"{label}.sh"
        path.write_text(script, encoding="utf-8")
        results.append((label, run_script(bash_path, str(path), str(tmp_path), 20_000)))

    first, second = results[0][1], results[1][1]
    assert (first.exit_code, first.stdout, first.stderr) == (
        second.exit_code,
        second.stdout,
        second.stderr,
    ), f"{name}：格式化改变了行为\n原：{first}\n新：{second}"
    assert first.exit_code == 0, f"{name} 的脚本本身没跑通：{first.stderr}"


def test_crlf_script_runs_the_same_after_normalization(tmp_path, bash_path):
    """带 CRLF 的脚本**归一之后**能跑通 —— 这就是"自动识别换行符"要的效果。"""
    from tu_shell_agent.shell_toolchain.execute import run_script

    original = "#!/usr/bin/env bash\nset -euo pipefail\necho hi\n".replace("\n", "\r\n")
    fixed = format_script(original).text
    path = tmp_path / "crlf.sh"
    path.write_text(fixed, encoding="utf-8")
    result = run_script(bash_path, str(path), str(tmp_path), 20_000)
    assert result.exit_code == 0, result.stderr
    assert result.stdout.strip() == "hi"


# ── 单行整理（回车 / 行内编辑那种即时动作）───────────────────────────


def test_tidy_line_fixes_tabs_and_trailing_space():
    assert tidy_line("\techo hi   ") == "    echo hi"
    assert tidy_line("    echo hi") == "    echo hi"
    assert tidy_line("\t\tx") == "        x"


def test_outcome_reports_nothing_when_nothing_changed():
    """没改动就别谎报"已格式化"（界面上的提示要与事实一致）。"""
    outcome = format_script(TIDY)
    assert outcome.text == TIDY
    assert outcome.changed is False and outcome.notes == ()


def test_empty_and_whitespace_only_input():
    """空输入格式化后**还是空**：不该被"整理"出一个换行来。

    全是空行的文本同理（末尾空行一律丢掉，这条与"文件尾恰好一个换行"是同一条规则的两面）。
    """
    assert format_script("").text == ""
    assert format_script("\n\n\n").text == ""
    assert format_script("   \n").text == ""
    assert format_script("echo hi\n\n\n").text == "echo hi\n"
