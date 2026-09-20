"""后端 agent 注册表：界面选一个后端，运行时就按它造适配器。

对外的三个入口（其余都是实现细节）：

- `available_backends()` —— 全部后端条目，界面用它填下拉；
- `backend_descriptor(id)` —— 按 id 取条目，未知 id 报错；
- `build_adapter(id, command, *, note=None)` —— 按 id 造出实现 `OpencodePort` 的适配器。

一个后端一个文件（见 `backends/`）：新增后端 = 新增文件 + 在 `registry.BACKENDS` 里加一行。
"""

from __future__ import annotations

from .descriptor import AdapterFactory, BackendDescriptor
from .registry import (
    BACKENDS,
    DEFAULT_BACKEND_ID,
    BackendError,
    ProbeResult,
    UnknownBackendError,
    available_backends,
    backend_descriptor,
    backend_ids,
    build_adapter,
    detect_environment,
    probe_backend,
    resolve_command,
)

__all__ = [
    "BACKENDS",
    "DEFAULT_BACKEND_ID",
    "AdapterFactory",
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
