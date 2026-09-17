"""提示词与结构化输出 schema（规格 §7.4、§7.5）。"""

from __future__ import annotations

from typing import Any

from ..types import FailureEvidence, ShellcheckFinding

OUTPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["script", "notes", "assumptions"],
    "properties": {
        "script": {
            "type": "string",
            "description": "完整的 shell 脚本内容；必须保留模板锚点注释；LF 换行；不要包含 markdown 代码块围栏",
        },
        "notes": {"type": "string", "description": "做了哪些取舍；方案中含糊之处如何处理"},
        "assumptions": {
            "type": "array",
            "items": {"type": "string"},
            "description": "你假定的前提",
        },
    },
}

# SYSTEM_RULES 已移出本模块：它只被 opencode_adapter/agent_file.py 消费（agent 定义正文），
# 留在这里会让 adapter 反向 import orchestrator，违反单向分层。见 opencode_adapter/agent_file.py。


def build_first_message(*, skeleton: str, anchors: tuple[str, ...], plan: str, run_dir: str) -> str:
    anchor_lines = "\n".join(f"- {anchor}" for anchor in anchors) or "（本模板无锚点）"
    return "\n".join(
        [
            "## 模板骨架（必须保留结构）",
            "```bash",
            skeleton.rstrip(),
            "```",
            "",
            "## 必须保留的锚点",
            anchor_lines,
            "",
            "## 方案文档",
            plan.strip(),
            "",
            "## 运行目录（脚本将在此目录下执行）",
            run_dir,
            "",
            "请按照上述硬规则，把方案实现进骨架，返回完整脚本。",
        ]
    )


def shellcheck_summary(findings: list[ShellcheckFinding] | tuple[ShellcheckFinding, ...]) -> str:
    counts: dict[str, int] = {}
    for finding in findings:
        counts[finding.code] = counts.get(finding.code, 0) + 1
    ordered = sorted(counts.items(), key=lambda item: (-item[1], item[0]))
    return "、".join(f"{code} ×{count}" for code, count in ordered)


def build_repair_message(
    *, evidence: FailureEvidence, anchors: tuple[str, ...], skeleton: str
) -> str:
    lines: list[str] = [f"## 第 {evidence.round} 轮失败反馈（阶段：{evidence.stage}）", ""]

    if evidence.contract is not None:
        contract = evidence.contract
        if contract.message:
            lines.append(f"上一轮没有产出可用脚本：{contract.message}")
            lines.append("请重新返回符合 schema 的 JSON（script 字段必须是完整脚本）。")
        else:
            lines.append(f"契约校验未通过：{contract.reason}")
            if contract.missing_anchors:
                lines.append(f"缺失的锚点：{'、'.join(contract.missing_anchors)}")
        lines.append("")

    if evidence.shellcheck:
        lines.append(f"shellcheck 汇总：{shellcheck_summary(evidence.shellcheck)}")
        lines.append("")
        for finding in evidence.shellcheck:
            lines.append(
                f"- {finding.code} 第 {finding.line} 行（{finding.level}）：{finding.message}"
            )
        lines.append("")

    if evidence.execute is not None:
        exec_info = evidence.execute
        suffix = "（超时被杀）" if exec_info.timed_out else ""
        lines.append(
            f"执行失败：退出码 {exec_info.exit_code}{suffix}，耗时 {exec_info.duration_ms}ms"
        )
        lines.append("")
        stderr_tail = exec_info.stderr_tail.rstrip()
        stdout_tail = exec_info.stdout_tail.rstrip()
        if stderr_tail:
            lines.extend(["stderr 尾部：", "```", stderr_tail, "```", ""])
        if stdout_tail:
            lines.extend(["stdout 尾部：", "```", stdout_tail, "```", ""])

    if skeleton.strip():
        lines.extend(["## 模板骨架（结构不得改动）", "```bash", skeleton.rstrip(), "```", ""])

    anchor_lines = "\n".join(f"- {anchor}" for anchor in anchors) or "（无）"
    lines.extend(["## 必须保留的锚点", anchor_lines, ""])
    lines.append("只修复上述问题，保持锚点与模板结构不变，返回完整脚本。")
    return "\n".join(lines)
