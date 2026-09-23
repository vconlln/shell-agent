"""后端注册表：把 `backends/` 下的条目收集起来、按 id 查、按 id 造适配器。

只做三件事：收集、查、构建（外加两个探测入口）。每个后端自己的常量都在它自己的文件里，
所以这里没有任何 `if backend == ...`：要改某个后端的行为，改那个文件。

**未知 id 一律报错，不静默回退 opencode**：静默回退会把"设置里写错了一个名字"变成
"用另一个 agent 跑了一次"，而用户以为自己在用 claude —— 这属于最不该出现的那种假象。
"""

from __future__ import annotations

from typing import Any, Callable

from ..shell_toolchain.detect import detect_all, detect_shell_deps, system_deps
from ..types import DetectionReport
from .backends import builtin, claude, codeagent, custom, opencode
from .cli_agent import ProbeResult, probe_version
from .descriptor import BackendDescriptor

# 界面下拉的顺序：默认后端在最前（用户不动它就是今天的行为），
# 内置 agent（直连模型 API，不需要装任何东西）紧随其后，自定义放最后。
BACKENDS: tuple[BackendDescriptor, ...] = (
    opencode.DESCRIPTOR,
    builtin.DESCRIPTOR,
    claude.DESCRIPTOR,
    codeagent.DESCRIPTOR,
    custom.DESCRIPTOR,
)

DEFAULT_BACKEND_ID = opencode.DESCRIPTOR.id

_BY_ID: dict[str, BackendDescriptor] = {descriptor.id: descriptor for descriptor in BACKENDS}


class BackendError(ValueError):
    """后端配置错误（未知 id、必填命令没填）。消息直接可以给用户看。"""


class UnknownBackendError(BackendError):
    """id 不在注册表里。"""


def backend_ids() -> tuple[str, ...]:
    return tuple(descriptor.id for descriptor in BACKENDS)


def available_backends() -> tuple[BackendDescriptor, ...]:
    """全部后端条目（顺序即界面下拉的顺序）。"""
    return BACKENDS


def backend_descriptor(backend_id: str) -> BackendDescriptor:
    """按 id 取条目；未知 id 抛出带可选值的错误。"""
    key = str(backend_id or "").strip()
    descriptor = _BY_ID.get(key)
    if descriptor is None:
        raise UnknownBackendError(
            f"未知的 agent 后端 {backend_id!r}；可选值：{'、'.join(backend_ids())}"
        )
    return descriptor


def resolve_command(backend_id: str, command: str = "", *, fallback: str = "") -> str:
    """解析这次要执行的命令：填了就用填的，其次用 `fallback`，最后用后端的默认命令。

    `fallback` 是"另一个可能填了命令的地方"：opencode 后端的路径一直由设置里的「组件路径」
    负责（老字段，用户已经填过它），所以那一栏的值通过 fallback 进来 —— 优先级写在**一处**，
    界面显示与实际执行才不会给出两个答案。

    必填却留空（自定义后端）时抛错，而不是返回空字符串：空命令要到子进程那一步才炸，
    那时错误信息离用户的操作已经很远了。
    """
    descriptor = backend_descriptor(backend_id)
    for candidate in (command, fallback):
        typed = str(candidate or "").strip()
        if typed:
            return typed
    if descriptor.default_command:
        return descriptor.default_command
    raise BackendError(
        f"{descriptor.display_name}后端必须填写命令：请在「设置 → 后端 agent」中填写"
        "可执行文件的命令名或完整路径。"
    )


def build_adapter(
    backend_id: str,
    command: str = "",
    *,
    note: Callable[[str], None] | None = None,
    api: dict[str, Any] | None = None,
) -> Any:
    """按 id 造适配器（同一套 `OpencodePort` 方法签名）。未知 id 报错，绝不回退。

    两条路：命令行后端用"命令"造；直连模型 API 的后端（内置 agent）用 `api` 配置造 ——
    它们需要的输入本来就不同，所以在这里分流，而不是让每个工厂都去猜。"""
    descriptor = backend_descriptor(backend_id)
    if descriptor.is_api:
        if descriptor.api_factory is None:  # pragma: no cover - is_api 就是它的定义
            raise BackendError(f"{descriptor.display_name} 后端缺少适配器工厂")
        config = dict(api or {})
        config.setdefault("note", note)
        return descriptor.api_factory(config)
    return descriptor.factory(resolve_command(backend_id, command), note)


def probe_backend(
    backend_id: str,
    command: str = "",
    *,
    timeout_s: float = 20.0,
    api: dict[str, Any] | None = None,
) -> ProbeResult:
    """检测该后端能不能用：命令行后端跑一次版本命令；API 后端真的问一次模型 API。

    刻意**不抛异常**：它的两个调用点（设置页的「检测」按钮、控制器开跑前的依赖检查）
    都要把失败原因显示给用户，异常只适合"调用方写错了"这类情形。
    """
    try:
        descriptor = backend_descriptor(backend_id)
    except BackendError as error:
        return ProbeResult(None, str(error))
    if descriptor.is_api:
        if descriptor.api_probe is None:  # pragma: no cover
            return ProbeResult(None, f"{descriptor.display_name} 后端缺少检测实现")
        return descriptor.api_probe(dict(api or {}))
    try:
        resolved = resolve_command(backend_id, command)
    except BackendError as error:
        return ProbeResult(None, str(error))
    tool, message = probe_version(
        resolved,
        descriptor.version_args,
        timeout_s=timeout_s,
        install_hint=descriptor.install_hint,
    )
    return ProbeResult(tool, message)


def detect_environment(
    backend_id: str,
    command: str = "",
    *,
    overrides: dict[str, str] | None = None,
    api: dict[str, Any] | None = None,
) -> DetectionReport:
    """按当前后端探测环境（自检页与开跑前的依赖检查共用这一个入口）。

    两条路的依赖**本来就不同**，所以这里按后端分流：

    - opencode：走既有的 `detect_all`（版本下限、凭据提示都是它专有的检查），行为与本功能
      加进来之前逐字一致；
    - 命令行后端：opencode 不再是依赖 —— 装了别的 agent 的机器上不该因为没装 opencode 就
      跑不动。于是只探测"该后端的命令 + bash + shellcheck"，其中 bash 与 shellcheck 仍然必需：
      引擎执行脚本用的是它们，换 agent 不换执行者。

    `DetectionReport.opencode` 这一栏承载"当前后端"的探测结果：字段名来自这份报告最早只有
    opencode 一种后端的年代，改名会牵动历史落盘的证据与多处界面代码，所以保留字段名、
    由界面按当前后端给出行标签。
    """
    descriptor = backend_descriptor(backend_id)
    deps = system_deps(overrides)
    if descriptor.is_api:
        # 直连模型 API 的后端：只需要 bash 与 shellcheck（引擎执行脚本用），
        # opencode 与"后端命令"都不是依赖 —— 它根本不装任何东西。
        bash, shellcheck, problems = detect_shell_deps(deps)
        result = probe_backend(backend_id, api=api)
        return DetectionReport(
            opencode=result.tool,
            bash=bash,
            shellcheck=shellcheck,
            problems=((() if result.ok else (result.message,)) + problems),
        )
    if not descriptor.is_cli:
        return detect_all(deps)

    result = probe_backend(backend_id, command, api=api)
    bash, shellcheck, problems = detect_shell_deps(deps)
    backend_problems = () if result.ok else (result.message,)
    return DetectionReport(
        opencode=result.tool,
        bash=bash,
        shellcheck=shellcheck,
        problems=(*backend_problems, *problems),
    )


__all__ = [
    "BACKENDS",
    "DEFAULT_BACKEND_ID",
    "BackendDescriptor",
    "BackendError",
    "ProbeResult",
    "UnknownBackendError",
    "available_backends",
    "backend_descriptor",
    "backend_ids",
    "build_adapter",
    "detect_environment",
    "probe_backend",
    "resolve_command",
]
