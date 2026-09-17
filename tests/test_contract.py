from tu_shell_agent.orchestrator.contract import (
    MAX_SCRIPT_BYTES,
    check_contract,
    extract_anchors,
    normalize_script,
)

ANCHORS = ("@@TU:BODY@@",)


def test_extract_anchors_in_order():
    assert extract_anchors("a\n# @@TU:BODY@@\nb\n# @@TU:EXTRA@@\n") == ("@@TU:BODY@@", "@@TU:EXTRA@@")


def test_contract_passes_for_non_empty_script_with_all_anchors():
    result = check_contract("#!/usr/bin/env bash\n# @@TU:BODY@@\necho hi\n", ANCHORS)
    assert result.ok is True
    assert result.missing_anchors == ()
    assert result.reason is None


def test_empty_script_fails():
    assert check_contract("   \n", ANCHORS).reason == "empty"


def test_missing_anchor_fails_and_lists_it():
    result = check_contract("#!/usr/bin/env bash\necho hi\n", ANCHORS)
    assert result.ok is False
    assert result.reason == "missing_anchor"
    assert result.missing_anchors == ("@@TU:BODY@@",)


def test_crlf_fails():
    assert check_contract("echo hi\r\n# @@TU:BODY@@\r\n", ANCHORS).reason == "has_crlf"


def test_oversized_script_fails():
    big = "# @@TU:BODY@@\n" + "x" * (MAX_SCRIPT_BYTES + 10) + "\n"
    assert check_contract(big, ANCHORS).reason == "too_large"


def test_normalize_strips_markdown_fence_and_forces_lf():
    fenced = "```bash\r\n#!/usr/bin/env bash\r\n# @@TU:BODY@@\r\n```\r\n"
    assert normalize_script(fenced) == "#!/usr/bin/env bash\n# @@TU:BODY@@\n"


def test_normalize_ensures_single_trailing_newline():
    assert normalize_script("echo hi") == "echo hi\n"
    assert normalize_script("echo hi\n\n\n") == "echo hi\n"
