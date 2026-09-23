"""起子进程时的平台参数：Windows 上**不许弹控制台窗口**。

用户实测（Windows）：

- **打开应用时闪一下黑框** —— 开窗之后后台自检要跑 `opencode --version` / `bash --version` /
  `shellcheck --version`；
- **与模型对话时闪** —— 每轮对话都要起一个后端 agent 的 CLI 进程（还有长驻的 `opencode serve`）。

原因是打包产物是**窗口程序**（`packaging/tu-shell-agent.spec` 里 `console=False`），
而 `CreateProcess` 在"父进程没有控制台"时会给子进程**新建一个控制台窗口** ——
窗口程序每次起子进程都会闪一下；`opencode serve` 那种长驻进程更是一直挂着一个黑框。

修法：所有子进程都带上 `CREATE_NO_WINDOW`（外加 `STARTUPINFO` 里的 `SW_HIDE` 兜底，
覆盖某些控制台继承的路径）。**集中在这一个模块里**：散在各处写 `creationflags=0x08000000`
迟早会漏掉一处，而漏掉的表现就是"偶发闪一下终端"，很难查。

因为窗口里的 flags 在 Linux 上是非法值，所以非 Windows 一律返回空 dict；
`platform` 可注入，让这条逻辑在 Linux 上也能被用例钉住。
"""

from __future__ import annotations

import os
import subprocess
from typing import Any

# CreateProcess 的标志位（`subprocess` 里在 Windows 上才有同名常量，这里给出取值兜底）
CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
CREATE_NEW_PROCESS_GROUP = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0x00000200)


def creation_flags(
    *,
    platform: str | None = None,
    new_process_group: bool = False,
) -> int:
    """Windows 上要带的 `creationflags`；其它平台是 0。

    `new_process_group` 给"要按整棵树杀掉的进程"用（执行脚本那条路）：它与
    `CREATE_NO_WINDOW` 可以并存，所以这里按位或，而不是二选一。
    """
    if (platform or os.name) != "nt":
        return 0
    flags = CREATE_NO_WINDOW
    if new_process_group:
        flags |= CREATE_NEW_PROCESS_GROUP
    return flags


def _hidden_startupinfo() -> Any | None:
    """要求"窗口隐藏"的 STARTUPINFO（拿不到就返回 None）。

    `CREATE_NO_WINDOW` 已经足够，但某些"继承父进程控制台"的路径不受它管；
    `STARTF_USESHOWWINDOW` + `SW_HIDE` 是那些路径上的兜底。`subprocess.STARTUPINFO`
    只在 Windows 的 Python 上存在，所以这里用 getattr 取。
    """
    factory = getattr(subprocess, "STARTUPINFO", None)
    if factory is None:
        return None
    try:
        info = factory()
    except Exception:  # noqa: BLE001 - 平台细节拿不到就交给 CREATE_NO_WINDOW
        return None
    use_show_window = getattr(subprocess, "STARTF_USESHOWWINDOW", 0x00000001)
    hide_window = getattr(subprocess, "SW_HIDE", 0)
    info.dwFlags |= use_show_window
    info.wShowWindow = hide_window
    return info


def no_window_kwargs(
    *,
    platform: str | None = None,
    new_process_group: bool = False,
) -> dict[str, Any]:
    """`subprocess.run/Popen` 的 kwargs：Windows 上加"不弹控制台"，其它平台是空 dict。

    用法：`subprocess.run(argv, **no_window_kwargs())` —— 或者合进已有的 kwargs 字典
    （`kwargs.update(no_window_kwargs(new_process_group=True))`）。
    """
    flags = creation_flags(platform=platform, new_process_group=new_process_group)
    if not flags:
        return {}
    kwargs: dict[str, Any] = {"creationflags": flags}
    info = _hidden_startupinfo()
    if info is not None:
        kwargs["startupinfo"] = info
    return kwargs
