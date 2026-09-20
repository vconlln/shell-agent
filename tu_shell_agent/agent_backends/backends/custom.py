"""自定义命令行后端条目：命令由用户填写，旗标约定与 Claude Code 一致。

引用 claude 条目的常量而不是复制一份，理由与 codeagent 那一条相同：权限收敛点只能有一份。
本条目与其他命令行后端的区别是 `requires_command=True` —— 没有默认命令可用，留空时必须
明确报错（探测与运行两处都报），而不是拿空字符串去撞子进程。
"""

from __future__ import annotations

from ..descriptor import BackendDescriptor
from .claude import CLI_SPEC, DENIED_TOOLS, make_adapter

INSTALL_HINT = "请在本页「命令」中填写可执行文件的命令名或完整路径"

DESCRIPTOR = BackendDescriptor(
    id="custom",
    display_name="自定义命令行",
    # 刻意留空：这个后端的存在意义就是"命令由用户决定"，给一个默认值只会掩盖没填这件事。
    default_command="",
    version_args=("--version",),
    needs_serve=False,
    requires_command=True,
    summary="命令由用户指定，旗标约定与 Claude Code 一致。",
    install_hint=INSTALL_HINT,
    factory=make_adapter,
    cli=CLI_SPEC,
)

__all__ = ["DENIED_TOOLS", "DESCRIPTOR", "INSTALL_HINT", "make_adapter"]
