"""命令行 agent 的文本产出契约（规格 §7.4 在命令行后端上的替代品）。

为什么需要它：opencode 那条路把"回一个 JSON"压在 API 的 `format: json_schema` 上 ——
结构由上游保证，适配器只管取字段。命令行 agent（Claude Code 那一类）没有这个开关，只能在
**提示词里写死格式要求**，再用**本模块的严格解析**把同一件事补回来：产出必须是可判定的，
任何半成品都不许往下走（解析出来的脚本会被引擎真的执行）。

与 `orchestrator/contract.py` 的分工：这个模块只把回复切成 script / notes / assumptions
三块；锚点（`# @@TU:BODY@@`）是否齐全仍由 `check_contract` / `extract_anchors` 判 —— 锚点校验
只有一处裁判，不因为换了后端就多出一套规则。

分层说明：本模块只 import 标准库与 `types`，不碰编排层的其它模块，因此它是编排层的一片叶子 ——
适配器层引用它既不会形成环，也不会把整条编排链（loop / ports / template_store）拖进适配器的 import。
"""

from __future__ import annotations

import re

from ..types import GeneratedScript

SCRIPT_BEGIN = "===TU-SCRIPT==="
NOTES_BEGIN = "===TU-NOTES==="
ASSUMPTIONS_BEGIN = "===TU-ASSUMPTIONS==="
END = "===TU-END==="

# 四段标记的顺序即解析顺序。缺任何一个都直接判这一轮失败 —— 半截产出比"什么都没有"更危险：
# 它看起来像一份脚本，但 assumptions 可能整段丢失（那就是把模型的假设静默丢掉）。
_MARKERS: tuple[str, ...] = (SCRIPT_BEGIN, NOTES_BEGIN, ASSUMPTIONS_BEGIN, END)

# 与 opencode 的 agent 定义（`opencode_adapter/agent_file.py` 的 SYSTEM_RULES）**同源的两份**，
# 不是复制粘贴：那一份要求"只在 JSON 的 script 字段里给脚本"，这一份要求"按四段标记给脚本"，
# 合成一份必然两边都别扭（各自的输出通道不同）。硬规则本身（锚点、结构、LF、不联网、不静默猜）
# 必须逐条对齐 —— 换后端不该换掉安全底线。
CLI_SYSTEM_RULES = """你是一个 shell 脚本生成器。用户会给你一份模板骨架和一份方案文档，你把方案实现进骨架。

硬规则：
1. 保留模板里的全部锚点注释（形如 # @@TU:NAME@@），一个都不能少、不能改名。
2. 保持模板的整体结构（shebang、set 选项、函数骨架、trap、参数解析）。
3. 不要引入网络下载、提权（sudo）、curl | bash、交互式命令。
4. 换行必须是 LF。
5. **不要执行任何命令、也不要写任何文件**：你只输出脚本文本，执行与落盘由调用方负责。
6. 方案含糊时选择保守实现，并把假设写进 assumptions 那一节，不要静默猜测。"""

# 追加到用户消息末尾的输出格式要求。写得这么啰嗦是有原因的：这份文本是命令行后端**唯一**的
# 结构约束来源（没有 schema 兜底），而模型的默认行为是"先说一段话、再把脚本放进 markdown 围栏"。
OUTPUT_INSTRUCTIONS = f"""## 输出格式（必须严格遵守，否则本次回复作废）

你的回复必须**只**包含下面四段标记与它们之间的内容，标记之外不要写任何文字：

1. 第一行是 {SCRIPT_BEGIN}；
2. 紧接着是脚本正文，直到 {NOTES_BEGIN} 为止。正文必须是**完整**的脚本：
   - 保留上面骨架里的全部锚点注释（形如 # @@TU:BODY@@），一个都不能少、不能改名；
   - 第一行是 shebang，换行用 LF；
   - 不要用 markdown 代码围栏包住正文（不要出现 ``` 这样的行）；
3. {NOTES_BEGIN} 到 {ASSUMPTIONS_BEGIN} 之间写取舍说明：你做了哪些选择、方案含糊之处如何处理；
4. {ASSUMPTIONS_BEGIN} 到 {END} 之间逐行列出你假定的前提，每行以 "- " 开头；
   没有前提时这一节留空，但标记本身必须保留；
5. 最后一行是 {END}。

再次强调：缺少任何一个标记、或脚本正文为空，本次回复都会被判为不合格并退回重做。"""

_FENCE_LINE = re.compile(r"^```[A-Za-z0-9_+-]*\s*$")
_NUMBERED = re.compile(r"^\d+[.)]\s*(.+)$")


class CliContractError(ValueError):
    """回复不符合文本契约。消息里必须说清**缺了哪一段**，它会原样回灌给模型再修一轮。"""


def with_output_instructions(message: str) -> str:
    """把输出格式要求追加到消息末尾（命令行后端的每一次生成都要带）。"""
    return f"{message.rstrip()}\n\n{OUTPUT_INSTRUCTIONS}"


def parse_generated_script(text: str) -> GeneratedScript:
    """把命令行 agent 的回复解析成 `GeneratedScript`；不合格就抛 `CliContractError`。

    刻意不做"尽力而为"的兜底（例如拿整段回复当脚本）：下游会真的执行这份脚本，
    而"猜出来的脚本"和"模型明确给出的脚本"之间的差别，正是这个契约要守住的东西。
    """
    raw = (text or "").replace("\r\n", "\n").replace("\r", "\n")
    missing = [marker for marker in _MARKERS if marker not in raw]
    if missing:
        raise CliContractError(
            "回复里缺少标记：" + "、".join(missing) + f"（四段标记缺一不可，依次是 "
            f"{SCRIPT_BEGIN} / {NOTES_BEGIN} / {ASSUMPTIONS_BEGIN} / {END}）"
        )

    # 顺序也要查：标记齐全但顺序颠倒时，"第一段"里装的其实是说明文字（甚至只装了一个标记），
    # 按段取就会把垃圾当脚本交下去 —— 而那份脚本会被真的执行。
    positions = [raw.find(marker) for marker in _MARKERS]
    if any(later <= earlier for earlier, later in zip(positions, positions[1:])):
        raise CliContractError(
            "四段标记的顺序不对"
            f"（应当依次出现 {SCRIPT_BEGIN}、{NOTES_BEGIN}、{ASSUMPTIONS_BEGIN}、{END}）"
        )

    script = _between(raw, SCRIPT_BEGIN, NOTES_BEGIN)
    notes = _between(raw, NOTES_BEGIN, ASSUMPTIONS_BEGIN)
    assumptions = _between(raw, ASSUMPTIONS_BEGIN, END)

    script = _strip_fences(script.strip()).strip()
    if not script:
        raise CliContractError(
            f"{SCRIPT_BEGIN} 与 {NOTES_BEGIN} 之间没有脚本文本（脚本正文不能为空）"
        )
    return GeneratedScript(
        script=script,
        notes=notes.strip(),
        assumptions=_parse_assumptions(assumptions),
    )


def _between(text: str, begin: str, end: str) -> str:
    """取 begin 与 end 之间的内容；顺序颠倒时给出明确的错误。

    标记出现多次时只认第一对：模型偶尔会把格式说明原样回显，取最后一段反而会拿到空正文。
    """
    start = text.find(begin)
    stop = text.find(end, start + len(begin))
    if stop < 0:
        raise CliContractError(f"标记顺序不对：{begin} 之后没有出现 {end}")
    return text[start + len(begin) : stop]


def _strip_fences(text: str) -> str:
    """去掉正文首尾的 markdown 围栏行。

    模型即使被明确要求"不要围栏"，也常常会加上 —— 而带围栏的脚本会被 shellcheck 判成语法
    错误（``` 不是合法命令），白烧一轮。所以这里做一次容错；正文**中间**的围栏不动：
    脚本里合法地出现 ``` 是可能的（例如 heredoc 里写 markdown），动了就是破坏用户的脚本。
    """
    lines = text.split("\n")
    if lines and _FENCE_LINE.match(lines[0].strip()):
        lines = lines[1:]
    if lines and _FENCE_LINE.match(lines[-1].strip()):
        lines = lines[:-1]
    return "\n".join(lines)


def _parse_assumptions(text: str) -> tuple[str, ...]:
    """assumptions 一节：每行一条（规定用 "- " 开头）。

    对 `*` / `1.` 这类别的列表记号也认：它们是同一种东西的不同写法，为记号差异丢掉一条
    真实假设，代价比多认几个记号大得多。空行忽略。
    """
    items: list[str] = []
    for line in text.split("\n"):
        stripped = line.strip()
        if not stripped:
            continue
        if stripped[0] in "-*+":
            item = stripped[1:].strip()
        else:
            numbered = _NUMBERED.match(stripped)
            item = (numbered.group(1) if numbered else stripped).strip()
        if item:
            items.append(item)
    return tuple(items)


__all__ = [
    "ASSUMPTIONS_BEGIN",
    "CLI_SYSTEM_RULES",
    "CliContractError",
    "END",
    "NOTES_BEGIN",
    "OUTPUT_INSTRUCTIONS",
    "SCRIPT_BEGIN",
    "parse_generated_script",
    "with_output_instructions",
]
