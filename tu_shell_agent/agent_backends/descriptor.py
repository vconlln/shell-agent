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

# 直连模型 API 的后端（内置 agent）用的是另一套配置：没有"命令"，只有 base/key/风格/模型。
# 单独一个工厂签名而不是硬塞进上面那个：命令行后端与 API 后端的输入本来就不同，
# 混在一起就要在每个工厂里判"这次传的到底是命令还是配置"。
ApiConfig = dict[str, Any]
ApiFactory = Callable[[ApiConfig], Any]
# 探测（「检测」按钮）：拿配置去真的问一次模型 API 是否可用。
ApiProbe = Callable[[ApiConfig], Any]
# 列出可用模型（「检测可用模型」与对话面板共用）。
ApiModelList = Callable[[ApiConfig], list]


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
    # 直连模型 API 的后端（内置 agent）：给一份配置就能造适配器 / 探测 / 列模型。
    # 三个分开，是因为它们的调用点不同（造适配器 / 设置页的「检测」/ 模型列表），
    # 合成一个函数再加参数枚举反而更绕。
    api_factory: ApiFactory | None = None
    api_probe: ApiProbe | None = None
    api_model_list: ApiModelList | None = None
    # API 后端的配置字段说明（设置页用它填占位文字与提示，不写死"openai"这类字眼）。
    api_base_hint: str = ""
    api_key_hint: str = ""

    @property
    def is_cli(self) -> bool:
        """这个后端是不是"命令行 agent"那一路（决定环境探测与依赖检查走哪条）。"""
        return self.cli is not None

    @property
    def is_api(self) -> bool:
        """这个后端是不是"直连模型 API"那一路（内置 agent）。"""
        return self.api_factory is not None
