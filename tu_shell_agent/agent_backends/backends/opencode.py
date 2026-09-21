"""opencode 后端条目：经 `opencode serve` 的 HTTP API 生成脚本（本应用的第一条路）。

这个文件只放 opencode 自己的东西（显示名、默认命令、版本探测命令、要不要 serve、工厂）。
注册表把四个这样的文件收集起来按 id 查，不在条目里做任何注册动作 ——
加一个后端 = 加一个文件 + 注册表里加一行。
"""

from __future__ import annotations

from typing import Any, Callable

from ..descriptor import BackendDescriptor

# 与 shell_toolchain/detect.py 的 `_INSTALL_HINTS["opencode"]` 说的是同一件事，但这里只讲
# "命令没找到该怎么办"：版本下限与凭据提示由那条既有的探测路径负责（那是 opencode 专有的，
# 命令行后端没有对应物，不该被塞进通用提示）。
INSTALL_HINT = (
    "请安装 Windows 原生 opencode"
    "（choco install opencode / scoop install opencode / npm i -g opencode-ai），"
    "本应用不支持仅存在于 WSL 的安装"
)


def make_adapter(command: str, note: Callable[[str], None] | None = None) -> Any:
    """工厂：返回 opencode 的 HTTP 适配器。

    import 放在函数里：`ui/**` 会在构造窗口时 import 注册表，而 `opencode_adapter` 顶层
    import 了 httpx —— 那不改分层（ui 并没有 import httpx），但没必要为"列一个下拉框"付它的代价。
    """
    from ...opencode_adapter import OpencodeAdapter

    return OpencodeAdapter(opencode_path=command, note=note)


DESCRIPTOR = BackendDescriptor(
    id="opencode",
    display_name="opencode",
    default_command="opencode",
    version_args=("--version",),
    needs_serve=True,
    summary="经 opencode serve 的 HTTP API 生成脚本，需要本机安装 opencode。",
    install_hint=INSTALL_HINT,
    factory=make_adapter,
    # opencode 的结构约束来自 API 的 JSON schema，没有命令行旗标可配，所以 cli 约定为空。
    cli=None,
    model_hint="留空则使用 opencode 的默认模型；未配置默认模型时会落到其免费档，而免费档仅限官方客户端调用。点「检测可用模型」可列出全部可用模型。",
)
