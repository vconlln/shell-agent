"""后端描述的数据结构。

单独一个模块（而不是放在注册表里）：每个后端条目都要 import 它来构造自己的描述，
而注册表要 import 这些条目 —— 结构放注册表里就会成环。这里只放"一个后端由哪些信息构成"，
不放任何收集/查找逻辑（那是 `registry.py` 的事）。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from .cli_agent import CliSpec

# 工厂签名：给命令与状态回调，返回实现 OpencodePort 的适配器实例。
# 命令由注册表解析出默认值之后传进来，所以工厂自己不必再判"留空时用什么"。
AdapterFactory = Callable[[str, Callable[[str], None] | None], Any]


@dataclass(frozen=True, slots=True)
class BackendDescriptor:
    """一个后端条目的全部信息。

    做成数据而不是"按 id 分支的 if/else"：界面要用它填下拉与提示行，控制器要用它决定是否
    起 serve、探测哪个命令、造哪个适配器 —— 三处读同一份描述，才不会出现"设置里显示 claude、
    实际按 opencode 探测"这种漂移。
    """

    id: str
    display_name: str
    default_command: str
    version_args: tuple[str, ...]
    needs_serve: bool
    summary: str
    install_hint: str
    factory: AdapterFactory
    # 命令必须由用户填写（自定义后端）：留空时要在探测与运行两处都给出明确错误，
    # 而不是拿一个空字符串去撞子进程。
    requires_command: bool = False
    # 该后端**没有**"列出可用模型"的能力时给出的建议值（可编辑下拉的候选项）。
    # 有列表能力的后端（opencode 的 `opencode models`）留空 —— 那是运行时探测出来的，
    # 不该在这里写死一份会过期的清单。
    model_suggestions: tuple[str, ...] = ()
    # 模型字段的说明语：告诉用户"这里的值会被原样传给谁、留空是什么意思"。
    model_hint: str = ""
    # 命令行后端的旗标约定；走 HTTP 的后端（opencode）为 None。
    cli: CliSpec | None = None

    @property
    def is_cli(self) -> bool:
        """这个后端是不是"命令行 agent"那一路（决定环境探测与依赖检查走哪条）。"""
        return self.cli is not None
