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


# ── 补充要求（用户在界面上临时追加的话）────────────────────────────────────


def test_first_message_includes_extra_requirements_section():
    """补充要求必须单独成段，并声明"与方案冲突时以本节为准"。

    不声明优先级的话，模型面对"方案说要清空 logs/、补充要求说别动 logs/"时只能猜。
    """
    message = build_first_message(
        skeleton="#!/usr/bin/env bash\n# @@TU:BODY@@\n",
        anchors=("@@TU:BODY@@",),
        plan="把 .log 清掉",
        run_dir="/tmp/run",
        extra="别动 logs/ 目录，这次只处理 .log",
    )

    assert "## 补充要求（本次运行临时追加，与方案冲突时以本节为准）" in message
    assert "别动 logs/ 目录" in message
    # 补充要求要在方案文档之后、运行目录之前（模型读的顺序：需求 → 追加 → 环境）
    assert message.index("## 方案文档") < message.index("## 补充要求")
    assert message.index("## 补充要求") < message.index("## 运行目录")


def test_empty_extra_adds_nothing():
    """没填补充要求时提示词一字不多（否则每轮都塞一个空段，白白占上下文）。"""
    base = build_first_message(
        skeleton="#!/usr/bin/env bash\n# @@TU:BODY@@\n", anchors=("@@TU:BODY@@",), plan="p", run_dir="/r"
    )
    with_empty = build_first_message(
        skeleton="#!/usr/bin/env bash\n# @@TU:BODY@@\n", anchors=("@@TU:BODY@@",), plan="p",
        run_dir="/r", extra="   \n  ",
    )
    assert base == with_empty
    assert "补充要求" not in base


def test_repair_message_also_carries_extra():
    """修复轮同样要带上补充要求：用户是在"这次运行"里提的，不该只对第一轮生效。"""
    evidence = FailureEvidence(round=1, stage="execute", execute=ExecuteEvidence(
        exit_code=1, timed_out=False, stdout_tail="boom", stderr_tail="", duration_ms=5))
    message = build_repair_message(
        evidence=evidence, anchors=("@@TU:BODY@@",), skeleton="#!/usr/bin/env bash\n", extra="别动 logs/"
    )
    assert "## 补充要求" in message and "别动 logs/" in message
