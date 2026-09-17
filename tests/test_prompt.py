from tu_shell_agent.orchestrator.prompt import (
    OUTPUT_SCHEMA,
    build_first_message,
    build_repair_message,
    shellcheck_summary,
)
from tu_shell_agent.types import (
    ContractEvidence,
    ExecuteEvidence,
    FailureEvidence,
    ShellcheckFinding,
)


def test_output_schema_requires_three_fields():
    assert OUTPUT_SCHEMA["required"] == ["script", "notes", "assumptions"]


def test_first_message_carries_skeleton_plan_rundir_and_anchors():
    message = build_first_message(
        skeleton="#!/usr/bin/env bash\n# @@TU:BODY@@\n",
        anchors=("@@TU:BODY@@",),
        plan="把所有 .log 文件压缩",
        run_dir="/tmp/runs/r1",
    )
    assert "把所有 .log 文件压缩" in message
    assert "@@TU:BODY@@" in message
    assert "/tmp/runs/r1" in message


def test_repair_message_carries_structured_shellcheck_evidence():
    evidence = FailureEvidence(
        round=1,
        stage="shellcheck",
        shellcheck=(
            ShellcheckFinding(
                code="SC2086", line=12, column=5, level="warning",
                message="Double quote to prevent globbing.",
            ),
        ),
    )
    message = build_repair_message(
        evidence=evidence, anchors=("@@TU:BODY@@",), skeleton="# @@TU:BODY@@\n"
    )
    assert "SC2086" in message
    assert "第 12 行" in message
    assert "@@TU:BODY@@" in message
    assert "完整脚本" in message


def test_repair_message_carries_execute_evidence():
    evidence = FailureEvidence(
        round=2,
        stage="execute",
        execute=ExecuteEvidence(
            exit_code=1, timed_out=False, duration_ms=12,
            stdout_tail="", stderr_tail="no such file",
        ),
    )
    message = build_repair_message(evidence=evidence, anchors=(), skeleton="")
    assert "退出码 1" in message
    assert "no such file" in message


def test_repair_message_carries_generation_error_text():
    evidence = FailureEvidence(
        round=1,
        stage="contract",
        contract=ContractEvidence(reason="empty", message="StructuredOutputError"),
    )
    message = build_repair_message(evidence=evidence, anchors=(), skeleton="")
    assert "StructuredOutputError" in message


def test_shellcheck_summary_groups_by_code():
    summary = shellcheck_summary(
        [
            ShellcheckFinding("SC2086", 1, 1, "warning", "a"),
            ShellcheckFinding("SC2086", 9, 1, "warning", "b"),
            ShellcheckFinding("SC2045", 3, 1, "warning", "c"),
        ]
    )
    assert "SC2086 ×2" in summary
    assert "SC2045 ×1" in summary
