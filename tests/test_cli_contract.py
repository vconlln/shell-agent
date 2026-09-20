"""命令行后端的文本契约：格式要求 + 解析器（`orchestrator/cli_contract.py`）。

为什么这些边界值得单独钉住：这条路上**没有 schema 兜底**，解析器的判定就是"这一轮算不算
有产出"的唯一依据。放得太松会把半截回复当成脚本交给引擎执行，放得太严会在模型加了一个
markdown 围栏时白烧一轮 —— 两侧都是真实成本，所以正常路径与四种缺段路径都要有用例。
"""

from __future__ import annotations

import pytest

from tu_shell_agent.orchestrator.cli_contract import (
    ASSUMPTIONS_BEGIN,
    END,
    NOTES_BEGIN,
    OUTPUT_INSTRUCTIONS,
    SCRIPT_BEGIN,
    CliContractError,
    parse_generated_script,
    with_output_instructions,
)
from tu_shell_agent.orchestrator.contract import extract_anchors

SCRIPT = "#!/usr/bin/env bash\nset -euo pipefail\n# @@TU:BODY@@\necho ok\n"


def _reply(script: str = SCRIPT, notes: str = "取舍说明", assumptions: str = "- 假设 A") -> str:
    return (
        f"{SCRIPT_BEGIN}\n{script}{NOTES_BEGIN}\n{notes}\n"
        f"{ASSUMPTIONS_BEGIN}\n{assumptions}\n{END}\n"
    )


def test_well_formed_reply_is_parsed_into_a_generated_script():
    generated = parse_generated_script(_reply())
    assert generated.script.strip() == SCRIPT.strip()
    assert generated.notes == "取舍说明"
    assert generated.assumptions == ("假设 A",)


def test_assumptions_accept_one_per_line_and_skip_blank_lines():
    """每行一条：空行忽略，别的列表记号（* / 1.）也认 —— 为记号差异丢掉一条真实假设代价更大。"""
    generated = parse_generated_script(
        _reply(assumptions="- 假设 A\n\n- 假设 B\n* 假设 C\n3. 假设 D")
    )
    assert generated.assumptions == ("假设 A", "假设 B", "假设 C", "假设 D")


def test_empty_assumptions_section_is_allowed():
    generated = parse_generated_script(_reply(assumptions=""))
    assert generated.assumptions == ()
    assert generated.script.strip()


def test_missing_any_marker_names_the_missing_one():
    """缺任何一段都要抛**明确**的异常（消息里说清缺了哪一段，它会被回灌给模型再修一轮）。"""
    for marker in (SCRIPT_BEGIN, NOTES_BEGIN, ASSUMPTIONS_BEGIN, END):
        broken = _reply().replace(marker, "", 1)
        with pytest.raises(CliContractError) as error:
            parse_generated_script(broken)
        assert marker in str(error.value)


def test_empty_script_body_is_rejected():
    with pytest.raises(CliContractError) as error:
        parse_generated_script(_reply(script="\n\n   \n"))
    assert "脚本" in str(error.value)


def test_fences_around_the_script_are_stripped():
    """模型常把正文包进 markdown 围栏 —— 围栏会让 shellcheck 直接判语法错误，必须剥掉。"""
    generated = parse_generated_script(_reply(script=f"```bash\n{SCRIPT}```"))
    assert generated.script.strip() == SCRIPT.strip()
    assert "```" not in generated.script
    assert extract_anchors(generated.script) == ("@@TU:BODY@@",)


def test_fences_inside_the_script_are_left_alone():
    """正文**中间**的围栏不能动：脚本里合法地出现 ``` 是可能的（heredoc 里写 markdown）。"""
    body = "#!/usr/bin/env bash\n# @@TU:BODY@@\ncat <<'EOF'\n```\nnot code\n```\nEOF\n"
    generated = parse_generated_script(_reply(script=body))
    assert "```" in generated.script
    assert generated.script.strip() == body.strip()


def test_markers_out_of_order_are_rejected():
    """标记顺序颠倒（先把说明写完了）不能猜：那时正文与说明分不清，猜错就是把说明当脚本执行。"""
    scrambled = f"{SCRIPT_BEGIN}\n{END}\n{NOTES_BEGIN}\n{ASSUMPTIONS_BEGIN}\n"
    with pytest.raises(CliContractError) as error:
        parse_generated_script(scrambled)
    assert NOTES_BEGIN in str(error.value)


def test_crlf_is_normalized():
    generated = parse_generated_script(_reply().replace("\n", "\r\n"))
    assert "\r" not in generated.script
    assert generated.assumptions == ("假设 A",)


def test_anchors_still_checked_by_the_engine_side():
    """契约只切三段；锚点校验仍是 `orchestrator/contract.py` 的事（换后端不换裁判）。"""
    generated = parse_generated_script(_reply(script="#!/usr/bin/env bash\necho 缺锚点\n"))
    assert extract_anchors(generated.script) == ()


def test_output_instructions_mention_every_marker():
    for marker in (SCRIPT_BEGIN, NOTES_BEGIN, ASSUMPTIONS_BEGIN, END):
        assert marker in OUTPUT_INSTRUCTIONS
    assert "锚点" in OUTPUT_INSTRUCTIONS
    assert "围栏" in OUTPUT_INSTRUCTIONS


def test_instructions_are_appended_without_dropping_the_message():
    message = "## 模板骨架\n```bash\necho hi\n```"
    combined = with_output_instructions(message)
    assert combined.startswith(message)
    assert combined.endswith(OUTPUT_INSTRUCTIONS)
