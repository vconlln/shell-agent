"""Claude Code（`claude`）后端条目：非交互模式 + stream-json 的 CLI。

本文件集中了这个后端**自己**的全部约定：显示名、默认命令、版本探测命令、模型旗标、
权限相关旗标与禁用工具清单、以及工厂返回哪个适配器。适配器（`cli_agent.CliAgentAdapter`）
只按这份常量拼命令行，自身不含任何"哪个后端"的分支 —— 换后端只换数据。

本机实测（claude 2.1.112）确认这些旗标可用：`-p` / `--output-format stream-json` / `--verbose` /
`--session-id <uuid>` / `--resume <id>` / `--append-system-prompt` / `--model` /
`--allowedTools` / `--disallowedTools` / `--permission-mode`。提示词经 stdin 传入（`-p` 支持管道）。
"""

from __future__ import annotations

from typing import Callable

from ..cli_agent import CliAgentAdapter, CliSpec
from ..descriptor import BackendDescriptor

INSTALL_HINT = (
    "请安装 Claude Code（npm i -g @anthropic-ai/claude-code），"
    "或在本页「命令」中填写它的完整路径"
)

# 禁用的工具：引擎是唯一的执行者，agent 只写不跑。
#
# 分四类逐项列出，而不是只写一个 `Bash`：这一栏是这个后端的**权限收敛点**（opencode 那条路
# 对应的是 agent 定义文件里逐键 deny 的 permission 块），漏一项就等于把一类能力留给了模型。
# 未列出的工具并不是"放行"：`--permission-mode default` 在非交互进程里没人能批准，
# 所以它们同样不会被静默执行。
DENIED_TOOLS: tuple[str, ...] = (
    # 执行类：本应用生成的脚本由引擎执行（shellcheck → 人工确认 → bash），agent 不许自己跑
    "Bash",
    "BashOutput",
    "KillShell",
    # 写入类：脚本由引擎从回复文本里解析后落盘，agent 不需要（也不许）直接改盘上的文件
    "Edit",
    "MultiEdit",
    "Write",
    "NotebookEdit",
    # 网络类：方案里不允许出现网络下载，生成阶段本身也不需要出网
    "WebFetch",
    "WebSearch",
    # 交互与编排类：非交互进程里没人能回答它们，留着只会让这一轮卡住或跑到别的方向去
    "AskUserQuestion",
    "Task",
    "TaskOutput",
    "TaskStop",
    "TodoWrite",
    "SendMessage",
    "SlashCommand",
    "Skill",
    "CronCreate",
    "CronDelete",
    "CronList",
    "ScheduleWakeup",
    "TeamCreate",
    "TeamDelete",
    "EnterWorktree",
)

# 显式允许的只读工具：读运行目录里的方案与骨架对生成有帮助，而它们改不了任何东西。
# 显式写出来是因为有的版本会把只读工具也拿去做权限询问，而非交互进程里没人能回答。
ALLOWED_TOOLS: tuple[str, ...] = ("Read", "Glob", "Grep")

CLI_SPEC = CliSpec(
    prompt_flag="-p",
    output_format_flag="--output-format",
    output_format="stream-json",
    verbose_flag="--verbose",
    session_new_flag="--session-id",
    session_resume_flag="--resume",
    model_flag="--model",
    system_prompt_flag="--append-system-prompt",
    allowed_tools_flag="--allowedTools",
    disallowed_tools_flag="--disallowedTools",
    permission_mode_flag="--permission-mode",
    # 留在需要批准的档位：非交互进程里没有批准者，等于"不批准"，而不是"默默放行"。
    permission_mode="default",
    denied_tools=DENIED_TOOLS,
    allowed_tools=ALLOWED_TOOLS,
)

def make_adapter(command: str, note: Callable[[str], None] | None = None) -> CliAgentAdapter:
    """本后端的适配器工厂：返回命令行适配器（就是 claude 那一类 CLI 的适配器）。

    差异全在 `CLI_SPEC` 里，适配器本身不认识"claude"这个名字 —— 所以 codeagent 与自定义
    后端能直接复用这个工厂，只是换一个命令名。
    """
    return CliAgentAdapter(command=command, spec=CLI_SPEC, note=note)


# Claude Code 的模型既可用别名（sonnet / opus / haiku），也可用完整名（如 claude-sonnet-4-6）。
# 这个后端没有"列出模型"的命令，所以给一组常用别名做候选；用户可以自己改成完整名。
MODEL_SUGGESTIONS: tuple[str, ...] = ("sonnet", "opus", "haiku")

MODEL_HINT = "模型名会原样传给 --model（可用别名，也可填完整名）。留空则使用该后端自己的默认模型；本后端不提供模型列表。"


DESCRIPTOR = BackendDescriptor(
    id="claude",
    display_name="Claude Code",
    default_command="claude",
    version_args=("--version",),
    needs_serve=False,
    summary="以非交互模式调用 claude 命令行，按 stream-json 读取输出。",
    install_hint=INSTALL_HINT,
    factory=make_adapter,
    cli=CLI_SPEC,
    model_suggestions=MODEL_SUGGESTIONS,
    model_hint=MODEL_HINT,
)

__all__ = ["ALLOWED_TOOLS", "CLI_SPEC", "DENIED_TOOLS", "DESCRIPTOR", "INSTALL_HINT", "make_adapter"]
