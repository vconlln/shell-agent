from tu_shell_agent.types import SEVERITY_RANK, blocks_run


def test_error_is_more_severe_than_warning():
    assert SEVERITY_RANK["error"] > SEVERITY_RANK["warning"]


def test_blocking_level_warning_blocks_error_and_warning_but_not_info():
    assert blocks_run("error", "warning") is True
    assert blocks_run("warning", "warning") is True
    assert blocks_run("info", "warning") is False
    assert blocks_run("style", "warning") is False


def test_blocking_level_error_only_blocks_error():
    assert blocks_run("error", "error") is True
    assert blocks_run("warning", "error") is False
