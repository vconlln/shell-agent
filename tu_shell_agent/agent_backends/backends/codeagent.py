"""codeagent 后端条目：Claude Code 的改造版，旗标约定与它一致（用户确认过）。

所以本文件**不复制**任何旗标常量：直接引用 claude 条目的 `CLI_SPEC`（含模型旗标、会话旗标、
禁用工具清单）。两份各抄一遍的话，权限收敛点就有了两个副本，改一处忘一处等于某一条后端
悄悄放开了执行类工具。这个后端与 claude 的差别只有一件事：命令名。
"""

from __future__ import annotations

from ..descriptor import BackendDescriptor
from .claude import CLI_SPEC, DENIED_TOOLS, make_adapter

INSTALL_HINT = "请确认 codeagent 已安装并在 PATH 中，或在本页「命令」中填写它的完整路径"

DESCRIPTOR = BackendDescriptor(
    id="codeagent",
    display_name="codeagent",
    default_command="codeagent",
    version_args=("--version",),
    needs_serve=False,
    summary="以非交互模式调用 codeagent 命令行，旗标约定与 Claude Code 一致。",
    install_hint=INSTALL_HINT,
    factory=make_adapter,
    cli=CLI_SPEC,
)

# `DENIED_TOOLS` / `make_adapter` 从 claude 条目再导出：这个后端禁用的是同一份清单、
# 用的是同一个适配器。放在 __all__ 里，是为了让"从这里就能查到它禁了什么"成立。
__all__ = ["DENIED_TOOLS", "DESCRIPTOR", "INSTALL_HINT", "make_adapter"]
