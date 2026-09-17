import dataclasses

import pytest

from tu_shell_agent.types import (
    SEVERITY_RANK,
    ContractEvidence,
    ExecuteEvidence,
    FailureEvidence,
    blocks_run,
)


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


def test_contract_evidence_is_frozen_and_defaults_message_to_none():
    evidence = ContractEvidence(reason="missing_anchor", missing_anchors=("@@TU:BODY@@",))
    assert evidence.message is None
    with pytest.raises(dataclasses.FrozenInstanceError):
        evidence.reason = "empty"  # type: ignore[misc]


def test_failure_evidence_carries_typed_sub_evidence():
    failure = FailureEvidence(
        round=2,
        stage="execute",
        execute=ExecuteEvidence(
            exit_code=1, timed_out=False, stdout_tail="", stderr_tail="boom", duration_ms=12
        ),
    )
    assert failure.execute is not None and failure.execute.exit_code == 1
    assert failure.contract is None
