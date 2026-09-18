"""生成项目级 opencode agent 定义（规格 §7.2）。

安全模型不押在总键上：源码级调研只确认了具体权限键，因此逐键显式 deny。
"""

from __future__ import annotations

from pathlib import Path
from ..filetext import write_text_lf

# 这条常量**只**被本模块消费（agent 定义的正文），所以它属于这里而不是 orchestrator：
# 分层是单向的 orchestrator → opencode_adapter，adapter 是叶子，反向 import 会让
# 叶子依赖上层包。放在这里同时消除了 agent_file → orchestrator.prompt 的反向依赖。
SYSTEM_RULES = """你是一个 shell 脚本生成器。用户会给你一份模板骨架和一份方案文档，你把方案实现进骨架。

硬规则：
1. 保留模板里的全部锚点注释（形如 # @@TU:NAME@@），一个都不能少、不能改名。
2. 保持模板的整体结构（shebang、set 选项、函数骨架、trap、参数解析）。
3. 不要引入网络下载、提权（sudo）、curl | bash、交互式命令。
4. 换行必须是 LF。
5. 只在返回 JSON 的 script 字段里给出完整脚本，不要额外解释。
6. 方案含糊时选择保守实现，并把假设写进 assumptions，不要静默猜测。"""

AGENT_NAME = "tu-shell-writer"

_DENY_KEYS = (
    "bash",
    "edit",
    "glob",
    "grep",
    "list",
    "lsp",
    "skill",
    "task",
    "todowrite",
    "question",
    "webfetch",
    "websearch",
    "doom_loop",
)


def _to_posix(path: str) -> str:
    """Windows 路径在 YAML 里容易被转义，统一转成正斜杠并去掉尾部斜杠。"""
    return path.replace("\\", "/").rstrip("/")


def render_agent_file(run_dir: str, model: str | None = None) -> str:
    run = _to_posix(run_dir)
    if not run:
        # 不能静默生成：空 run_dir 会让 read 白名单退化成 "/**": allow，
        # 而它比 "*": deny 更具体 → 覆盖默认拒绝 → 整盘可读。宁可报错，不能放行。
        raise ValueError("run_dir 不能为空：否则 read 白名单会退化成 /**（整盘可读）")
    lines = [
        "---",
        "description: 把方案文档实现进给定的 shell 模板骨架；只读运行目录，不写文件、不执行命令。",
        "mode: primary",
    ]
    if model:
        lines.append(f"model: {model}")
    lines.append("permission:")
    lines.extend(f"  {key}: deny" for key in _DENY_KEYS)
    lines.extend(
        [
            "  external_directory: deny",
            "  read:",
            '    "*": deny',
            f'    "{run}/**": allow',
            "---",
            SYSTEM_RULES,
            "",
        ]
    )
    return "\n".join(lines)


def write_agent_file(run_dir: str, model: str | None = None) -> str:
    content = render_agent_file(run_dir, model)  # 先渲染：空 run_dir 要在落盘/建目录之前就抛错
    path = Path(run_dir, ".opencode", "agents", f"{AGENT_NAME}.md")
    path.parent.mkdir(parents=True, exist_ok=True)
    write_text_lf(path, content)
    return str(path)
