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


def extra_section(extra: str) -> list[str]:
    """用户现场补充的要求，单独成段。

    为什么不直接拼进方案文档：这两者的**权威性不同** —— 方案文档是"需求"，补充要求是
    用户在这一次运行前临时追加的话（"这次别动 logs/ 目录"）。混在一段里，模型无法判断
    冲突时听谁的；落盘时也分不清"原始方案"与"临时追加"。所以单独一段并写明优先级。
    """
    if not extra.strip():
        return []
    return [
        "## 补充要求（本次运行临时追加，与方案冲突时以本节为准）",
        extra.strip(),
        "",
    ]


def build_first_message(
    *,
    skeleton: str,
    anchors: tuple[str, ...],
    plan: str,
    run_dir: str,
    extra: str = "",
) -> str:
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
            *extra_section(extra),
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
    *,
    evidence: FailureEvidence,
    anchors: tuple[str, ...],
    skeleton: str,
    extra: str = "",
) -> str:
    """修复轮的消息：**以"你上一轮写的那一版"为主体**，失败证据说明"要改哪里"。

    用户实测报过一个真问题：一出错就"退回模板的 shell 代码"。根因就在这条消息里 ——
    它原来只给了失败证据与模板骨架，**唯独没有上一轮那份脚本**，末尾还写着"保持锚点与模板
    结构不变，返回完整脚本"。模型手里只有骨架，自然照着骨架重写一遍。
    现在把上一轮脚本放在最前面并写明"在这一版上改"，骨架降级成"仅供参考的结构要求"。
    """
    lines: list[str] = [f"## 第 {evidence.round} 轮失败反馈（阶段：{evidence.stage}）", ""]

    previous = evidence.script.strip()
    if previous:
        lines.extend(
            [
                "## 你上一轮写的脚本（**在这一版上修改**）",
                "```bash",
                previous,
                "```",
                "",
                "**要求**：只改下面指出的问题，保留其它已经正确的部分；"
                "不要在模板骨架上重写、不要把内容换成模板里的占位实现。"
                "返回的必须是完整脚本（包含上一轮里没出问题的那些行）。",
                "",
            ]
        )

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
        # 骨架只作**结构参考**：它排在上一轮脚本之后，并明说不要拿它替换内容
        # （以前它是这条消息里唯一像"脚本"的东西，于是成了模型抄回去的对象）
        lines.extend(
            [
                "## 模板骨架（仅供参考：结构要求来自它，**不要**用它替换你上一轮的脚本）",
                "```bash",
                skeleton.rstrip(),
                "```",
                "",
            ]
        )

    anchor_lines = "\n".join(f"- {anchor}" for anchor in anchors) or "（无）"
    lines.extend(["## 必须保留的锚点", anchor_lines, ""])
    lines.extend(extra_section(extra))
    if previous:
        lines.append(
            "在你**上一轮那份脚本**的基础上只修复上述问题，返回完整脚本"
            "（锚点与模板结构保持不变，但内容是上一版的延续，不是模板骨架）。"
        )
    else:
        # 生成阶段就失败、或续跑时拿不到上一版：这时确实只能从骨架来（如实说明，不装）
        lines.append("只修复上述问题，保持锚点与模板结构不变，返回完整脚本。")
    return "\n".join(lines)
