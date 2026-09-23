"""Windows 上"这个组件到底装在哪"：进程 PATH 之外还要看注册表，并补齐扩展名。

用户实测（Windows）："你这个在 windows 上自动获取环境变量里的组件路径获取不到，windows 有环境变量，
但是仍然无法自动获取"。这里有两类真实成因，都在这个模块里解决：

1. **进程的 PATH 可能是旧的**。Windows 上 `PATH` 是在登录/启动进程时复制一份的：用户在"系统属性"
   里刚加完目录，**已经在运行的 explorer.exe 还是旧环境**，从它启动的 GUI 程序自然也拿不到新目录
   （用户的终端里能看到，是因为终端是新开的）。注册表里的 `Path` 才是"当前设置"，
   所以这里把 `HKCU\\Environment` 与 `HKLM\\...\\Session Manager\\Environment` 的 `Path`
   也读进来，与进程 PATH 合并后再查找。

2. **`shutil.which` 依赖 `PATHEXT`**。npm / scoop / pipx 装的 CLI 是 `.cmd` 包壳，没有扩展名可查时
   `which` 要靠 `PATHEXT` 才知道试 `.CMD`；而某些启动方式（服务、脚本、被上层程序启动）会把它清掉，
   于是"明明在 PATH 里却找不到"。所以这里在"名字没有扩展名"时**显式**按
   `.exe` → `.cmd` → `.bat` → `.ps1` → 无扩展名 依次试一遍，不依赖 `PATHEXT`。

`read_value` / `environ` / `is_file` 都可注入，于是这段 Windows 专有逻辑在 Linux 上也能被用例钉住。
"""

from __future__ import annotations

import os
from collections.abc import Callable, Mapping
from pathlib import Path

# 进程 PATH 之外还要读的注册表位置（用户级在前：与"用户装的东西优先"一致）
_REGISTRY_KEYS: tuple[tuple[str, str], ...] = (
    ("HKCU", "Environment"),
    ("HKLM", r"SYSTEM\CurrentControlSet\Control\Session Manager\Environment"),
)

# 名字没有扩展名时依次尝试的后缀。不依赖 PATHEXT（见模块说明）。
_WINDOWS_EXTS: tuple[str, ...] = (".exe", ".cmd", ".bat", ".ps1", ".com", "")

# 系统路径分隔符（Windows 用分号；这里写死，因为它描述的是 Windows 的 PATH 格式）
_WINDOWS_PATH_SEP = ";"


def is_windows(platform: str | None = None) -> bool:
    """平台判断：`os.name` 是 `"nt"`，而探测层用的是 `"win32"` —— 两种写法都要认。

    （踩过：只看 `"nt"` 时，探测层传进来的 `"win32"` 会被当成 POSIX，
    合并 PATH 与补扩展名整条逻辑就静默不生效。）
    """
    return (platform or os.name) in {"nt", "win32"}


def _env_lookup(environ: Mapping[str, str], name: str) -> str:
    """环境变量取值：Windows 的键名大小写不敏感，几种写法都试。"""
    for candidate in (name, name.upper(), name.lower()):
        value = environ.get(candidate)
        if value:
            return str(value)
    return ""


def expand_windows_vars(value: str, environ: Mapping[str, str]) -> str:
    """展开 `%NAME%`（自己实现，不用 `os.path.expandvars` —— 后者在 Linux 上不认 `%NAME%`）。

    注册表里的 `Path` 常常写成 `%SystemRoot%\\system32;...`，不展开就等于拿到一堆没用的字符串。
    """
    out: list[str] = []
    index = 0
    while index < len(value):
        char = value[index]
        if char != "%":
            out.append(char)
            index += 1
            continue
        end = value.find("%", index + 1)
        if end < 0:                     # 落单的 %：原样保留
            out.append(value[index:])
            break
        name = value[index + 1 : end]
        replacement = _env_lookup(environ, name) if name else ""
        out.append(replacement if replacement else value[index : end + 1])
        index = end + 1
    return "".join(out)


def _winreg_read(root: str, subkey: str, name: str) -> str:
    """读注册表字符串值；读不到返回空串（无权、键不存在、非 Windows 都算读不到）。"""
    try:
        import winreg
    except ImportError:
        return ""
    hive = winreg.HKEY_CURRENT_USER if root == "HKCU" else winreg.HKEY_LOCAL_MACHINE
    try:
        with winreg.OpenKey(hive, subkey) as key:
            value, _kind = winreg.QueryValueEx(key, name)
    except OSError:
        return ""
    return str(value or "")


def registry_path_entries(
    *,
    environ: Mapping[str, str] | None = None,
    read_value: Callable[[str, str, str], str] | None = None,
) -> list[str]:
    """注册表里配置的 PATH 目录（用户级在前），已展开 `%NAME%`、去空、按 Windows 规则去重。"""
    env = environ if environ is not None else dict(os.environ)
    reader = read_value or _winreg_read
    entries: list[str] = []
    seen: set[str] = set()
    for root, subkey in _REGISTRY_KEYS:
        raw = reader(root, subkey, "Path")
        if not raw:
            continue
        for chunk in expand_windows_vars(raw, env).split(_WINDOWS_PATH_SEP):
            directory = chunk.strip().strip('"')
            if not directory:
                continue
            key = directory.lower()
            if key in seen:
                continue
            seen.add(key)
            entries.append(directory)
    return entries


def effective_path(
    *,
    environ: Mapping[str, str] | None = None,
    read_value: Callable[[str, str, str], str] | None = None,
    platform: str | None = None,
) -> str:
    """查找可执行文件时该用的 PATH：进程 PATH + 注册表里那些"进程还不知道"的目录。

    顺序上进程 PATH 在前：用户当前会话里能跑的东西优先命中；注册表里多出来的目录补在后面。
    """
    env = environ if environ is not None else dict(os.environ)
    current = _env_lookup(env, "PATH")
    if not is_windows(platform):
        return current
    system = platform or os.name
    # 切分与拼接必须用**同一个**分隔符：Windows 的 PATH 用分号，
    # 而 `os.pathsep` 描述的是"当前进程所在平台"（在别的平台上跑这段逻辑时会切成碎片）。
    entries = [chunk for chunk in current.split(_WINDOWS_PATH_SEP) if chunk.strip()]
    seen = {entry.strip().strip('"').lower() for entry in entries}
    for directory in registry_path_entries(environ=env, read_value=read_value):
        if directory.lower() not in seen:
            seen.add(directory.lower())
            entries.append(directory)
    return _WINDOWS_PATH_SEP.join(entries)


def _which_with_extensions(
    name: str,
    directories: list[str],
    exists: Callable[[str], bool],
    platform: str,
) -> str | None:
    """在给定目录里按扩展名逐个试（名字已带扩展名时只试它本身）。"""
    suffixes = ("",) if Path(name).suffix else (_WINDOWS_EXTS if is_windows(platform) else ("",))
    for directory in directories:
        for suffix in suffixes:
            candidate = str(Path(directory) / f"{name}{suffix}")
            if exists(candidate):
                return candidate
    return None


def find_executable(
    name: str,
    *,
    environ: Mapping[str, str] | None = None,
    read_value: Callable[[str, str, str], str] | None = None,
    is_file: Callable[[str], bool] | None = None,
    platform: str | None = None,
) -> str | None:
    """像 shell 那样在 PATH 里找可执行文件，但两点更宽（见模块说明）：

    1. PATH 取"进程 PATH + 注册表 PATH"的合并结果（进程的可能是旧的）；
    2. 名字没带扩展名时显式试 `.exe/.cmd/.bat/.ps1/.com`，不依赖 `PATHEXT`。

    已经是路径（含分隔符）时直接看它在不在。
    """
    text = (name or "").strip().strip('"')
    if not text:
        return None
    exists = is_file or os.path.isfile
    system = platform or os.name
    if any(separator in text for separator in ("/", "\\")) or (os.sep and os.sep in text):
        return text if exists(text) else None
    env = environ if environ is not None else dict(os.environ)
    raw_path = effective_path(environ=env, read_value=read_value, platform=system)
    separator = _WINDOWS_PATH_SEP if is_windows(system) else os.pathsep
    directories = [chunk.strip().strip('"') for chunk in raw_path.split(separator)]
    directories = [directory for directory in directories if directory]
    return _which_with_extensions(text, directories, exists, system)
