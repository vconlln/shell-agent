"""把"命令 + 参数"变成**真正能起进程的 argv**。

Windows 上有一个必须处理的坑，用户实测撞到过（"我后端有 codeagent，但是检测不到"）：

- `shutil.which("codeagent")` 在 Windows 上会按 PATHEXT 找到 `codeagent.cmd`（npm/包管理器装的
  CLI 基本都是 `.cmd` + `.ps1` 两个包壳），**找得到**；
- 但 `subprocess` 最终走 `CreateProcess`，而它**不执行 `.cmd`/`.bat`** ——
  直接起会得到 `[WinError 193] %1 is not a valid Win32 application`（或 `FileNotFoundError`），
  表现就是"这个后端检测不到"，而用户明明装了它。

所以 `.cmd` / `.bat` 要经 `cmd.exe /c` 起，`.ps1` 要经 `powershell -File` 起。
探测（`cli_agent.probe_version`）与真正跑任务（`CliAgentAdapter`）必须走**同一个**转换，
否则会出现"检测通过、一跑就失败"这种更难查的错。
"""

from __future__ import annotations

import os
import shutil
from collections.abc import Callable, Sequence

# Windows 的 cmd 解释器：优先用环境变量里的 %COMSPEC%，拿不到再退回 cmd.exe。
_WINDOWS_SHELL_EXTS = (".cmd", ".bat")
_WINDOWS_PS_EXTS = (".ps1",)


def resolve_command(command: str, *, which: Callable[[str], str | None] = shutil.which) -> str | None:
    """像 shell 那样找命令；找不到返回 None。

    单独抽出来是为了让"找不到"与"找到了但起不来"能给出不同的提示（前者要安装建议，
    后者是环境问题）—— 这两种情况在界面上必须能区分。
    """
    text = (command or "").strip()
    if not text:
        return None
    # 已经是路径（含分隔符）就直接看它存不存在，别再交给 which 猜
    if os.sep in text or (os.altsep and os.altsep in text):
        return text if os.path.exists(text) else None
    return which(text)


def invocation_argv(
    command: str,
    args: Sequence[str] = (),
    *,
    os_name: str | None = None,
    environ: dict[str, str] | None = None,
    which: Callable[[str], str | None] = shutil.which,
) -> list[str]:
    """命令 + 参数 → 可执行的 argv（Windows 的 `.cmd`/`.bat`/`.ps1` 包壳在这里被正确包裹）。

    `os_name` / `environ` / `which` 都可注入：这条逻辑的分支只能在 Windows 上跑到，
    但我们要求它在 Linux 上也能被用例钉住（否则又要靠"用户机器上才发现"）。
    """
    platform = os_name if os_name is not None else os.name
    env = environ if environ is not None else dict(os.environ)
    resolved = resolve_command(command, which=which) or (command or "").strip()
    argv = [resolved, *args]
    if platform != "nt":
        return argv

    lowered = resolved.lower()
    if lowered.endswith(_WINDOWS_SHELL_EXTS):
        # cmd.exe /c "<路径>" <参数...>：把包壳交给它自己的解释器
        return [env.get("COMSPEC") or "cmd.exe", "/c", *argv]
    if lowered.endswith(_WINDOWS_PS_EXTS):
        return [
            "powershell.exe",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            *argv,
        ]
    return argv
