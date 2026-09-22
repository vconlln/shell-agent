"""把"命令 + 参数"变成**真正能起进程的 argv**，以及"命令到底装在哪"。

Windows 上有一个必须处理的坑，用户实测撞到过（"我后端有 codeagent，但是检测不到"）：

- `shutil.which("codeagent")` 在 Windows 上会按 PATHEXT 找到 `codeagent.cmd`（npm/包管理器装的
  CLI 基本是 `.cmd` + `.ps1` 两个包壳），**找得到**；
- 但 `subprocess` 最终走 `CreateProcess`，而它**不执行 `.cmd`/`.bat`** ——
  直接起会得到 `[WinError 193] %1 is not a valid Win32 application`（或 `FileNotFoundError`），
  表现就是"这个后端检测不到"，而用户明明装了它。

所以 `.cmd` / `.bat` 要经 `cmd.exe` 起，`.ps1` 要经 `powershell -File` 起。
探测（`cli_agent.probe_version`）与真正跑任务（`CliAgentAdapter`）必须走**同一个**转换，
否则会出现"检测通过、一跑就失败"这种更难查的错。

## 为什么还要"直接解析包壳"

`.cmd` 只能交给 `cmd.exe`，而 cmd 会把命令行**再解析一遍**：裸的 `&` `|` `<` `>` `^` `(` `)`
会被它当成语法，`%NAME%` 会被展开，**换行更是命令分隔符** ——
而我们的提示词里既有标点也有换行（系统提示词是多行的），经 cmd 传就会碎掉。

npm 装的包壳内容非常固定：

```bat
@ECHO off
...
endLocal & goto #_undefined_# 2>NUL || title %COMSPEC% & "%_prog%"  "%dp0%\\node_modules\\codeagent\\cli.js" %*
```

也就是说它自己就写着"用哪个 node、跑哪个 js"。把这个读出来之后就能**绕开 cmd**：
`[node.exe, ...\\cli.js, *参数]` 交给 `CreateProcess`，参数走标准引号规则，
标点与换行都安全（npm 包壳是 Windows 上占绝大多数的安装方式）。
读不出来（不是 npm 形状）才退回 `cmd.exe /d /s /c`，并且对参数做 cmd 转义。

## 找不到命令时还会去哪找

GUI 程序（尤其是双击 exe 起的）拿到的 PATH 可能比用户的终端少几条 —— 用户"明明装了却找不到"
多半就是这个。所以 `which` 失败后再按常见安装位置找一遍：
`%APPDATA%\\npm`（npm 全局）、`%LOCALAPPDATA%\\pnpm`、`scoop\\shims`、`.cargo\\bin`、
`.local\\bin`（pipx/uv）、`Program Files\\nodejs` 等。
"""

from __future__ import annotations

import os
import re
import shutil
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path

# Windows 的 cmd 解释器：优先用环境变量里的 %COMSPEC%，拿不到再退回 cmd.exe。
_WINDOWS_SHELL_EXTS = (".cmd", ".bat")
_WINDOWS_PS_EXTS = (".ps1",)

# 找不到命令时按这些目录再找一遍（相对环境变量的子路径）。
_WINDOWS_EXTRA_DIRS: tuple[tuple[str, ...], ...] = (
    ("APPDATA", "npm"),                       # npm -g 的包壳就放这儿
    ("LOCALAPPDATA", "npm"),
    ("APPDATA", "pnpm"),
    ("LOCALAPPDATA", "pnpm"),
    ("USERPROFILE", ".local", "bin"),         # pipx / uv / 手装脚本
    ("USERPROFILE", ".cargo", "bin"),         # cargo install
    ("USERPROFILE", "scoop", "shims"),
    ("LOCALAPPDATA", "Programs", "nodejs"),   # 便携版 node
    ("LOCALAPPDATA", "Programs", "Python", "Scripts"),
    ("ProgramFiles", "nodejs"),
    ("ProgramFiles(x86)", "nodejs"),
    ("ProgramFiles", "Git", "usr", "bin"),
)

# 同一目录里多个候选时的优先级：原生 exe 最稳，其次是包壳。
_WINDOWS_EXTS = (".exe", ".cmd", ".bat", ".ps1", "")

# npm 包壳里"真正要跑的那个 js"：形如 "%dp0%\node_modules\pkg\cli.js"
_SHIM_SCRIPT = re.compile(r'"%~?dp0%?[\\/]([^"\r\n]+?\.(?:js|mjs|cjs))"', re.IGNORECASE)


def _env_value(env: Mapping[str, str], key: str) -> str:
    """环境变量取值：Windows 的键名大小写不敏感，两种写法都试。"""
    for candidate in (key, key.upper(), key.lower()):
        value = env.get(candidate)
        if value:
            return str(value)
    return ""


def windows_candidates(
    command: str,
    *,
    environ: Mapping[str, str] | None = None,
) -> list[str]:
    """按 Windows 常见安装位置列出候选路径（纯函数，不碰文件系统，便于用例钉住）。"""
    env = environ if environ is not None else dict(os.environ)
    text = (command or "").strip()
    if not text:
        return []
    name = Path(text).name
    # 注意：`_WINDOWS_EXTS` 末尾那一项是空串（"没有扩展名"也是一种候选），所以这里不能用
    # "在后缀表里"来判定"用户已经写了扩展名" —— 否则 `codeagent` 只会查一个没有后缀的文件，
    # 而真正装着的 `codeagent.cmd` 永远找不到（第一版就是这么错的）。
    has_ext = Path(name).suffix != ""
    out: list[str] = []
    for parts in _WINDOWS_EXTRA_DIRS:
        base = _env_value(env, parts[0])
        if not base:
            continue
        directory = Path(base).joinpath(*parts[1:])
        candidates = [name] if has_ext else [f"{name}{ext}" for ext in _WINDOWS_EXTS]
        for candidate in candidates:
            if candidate:
                out.append(str(directory / candidate))
    return out


def resolve_command(
    command: str,
    *,
    which: Callable[[str], str | None] = shutil.which,
    environ: Mapping[str, str] | None = None,
    is_file: Callable[[str], bool] | None = None,
    platform: str | None = None,
) -> str | None:
    """像 shell 那样找命令；找不到返回 None。

    单独抽出来是为了让"找不到"与"找到了但起不来"能给出不同的提示（前者要安装建议，
    后者是环境问题）—— 这两种情况在界面上必须能区分。

    Windows 上 `which` 失败后还会去常见安装目录找一遍：GUI 进程的 PATH 常常比终端少几条，
    而用户"明明装了却找不到"基本都是这个原因。
    """
    text = (command or "").strip()
    if not text:
        return None
    # 已经是路径（含分隔符）就直接看它存不存在，别再交给 which 猜
    if os.sep in text or "/" in text or "\\" in text:
        exists = is_file or os.path.isfile
        return text if exists(text) else None
    found = which(text)
    if found:
        return found
    if (platform or os.name) != "nt":
        return None
    exists = is_file or os.path.isfile
    for candidate in windows_candidates(text, environ=environ):
        if exists(candidate):
            return candidate
    return None


# ── npm 包壳：直接读出"程序 + 脚本" ────────────────────────────────────


def shim_script_path(shim: str) -> str | None:
    """从 `.cmd`/`.bat` 包壳里读出它真正要跑的那个 js 的路径（读不出来返回 None）。"""
    try:
        text = Path(shim).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    match = _SHIM_SCRIPT.search(text)
    if match is None:
        return None
    relative = match.group(1).replace("\\", "/").lstrip("/")
    script = Path(shim).parent.joinpath(*relative.split("/"))
    return str(script) if script.is_file() else None


def shim_program(
    shim: str,
    *,
    which: Callable[[str], str | None] = shutil.which,
    is_file: Callable[[str], bool] | None = None,
) -> str | None:
    """包壳要用哪个解释器跑那个 js：包壳同目录的 node.exe，否则 PATH 里的 node。"""
    exists = is_file or os.path.isfile
    beside = Path(shim).parent / "node.exe"
    if exists(str(beside)):
        return str(beside)
    for name in ("node.exe", "node"):
        found = which(name)
        if found:
            return found
    return None


def direct_shim_argv(
    shim: str,
    args: Sequence[str] = (),
    *,
    which: Callable[[str], str | None] = shutil.which,
    is_file: Callable[[str], bool] | None = None,
) -> list[str] | None:
    """npm 风格包壳 → `[node, 真正的 js, *参数]`；不是这种形状就返回 None。

    绕开 cmd 的意义见模块说明：参数里的标点与**换行**经 cmd 会被吃掉或当成命令分隔符，
    而我们的系统提示词就是多行的。
    """
    script = shim_script_path(shim)
    if script is None:
        return None
    program = shim_program(shim, which=which, is_file=is_file)
    if program is None:
        return None
    return [program, script, *args]


# ── 兜底：交给 cmd.exe（参数做 cmd 转义）──────────────────────────────

# cmd 的语法字符：不在引号里时必须用 ^ 转义
_CMD_META = "&|<>^()"


def cmd_quote(arg: str) -> str:
    """「这个参数在 cmd 命令行里怎么写」。

    两套规则：
    - 含空格/制表符/引号的参数用双引号包起来：引号内的 `& | < > ^ ( )` 是字面量，
      但 `%` **仍然**会展开，所以 `%` 一律写成 `%%`（包壳是 .bat，`%%` 才是字面量 `%`）；
    - 不含这些字符的参数用 `^` 转义语法字符。
    换行在 cmd 里是命令分隔符，只能退化成空格（能跑起来，但内容不保真 ——
    这也是为什么优先走 `direct_shim_argv`）。
    """
    text = str(arg).replace("\r\n", " ").replace("\n", " ").replace("\r", " ")
    if any(character in text for character in ' \t"'):
        return '"' + text.replace('"', '""').replace("%", "%%") + '"'
    out: list[str] = []
    for character in text:
        if character in _CMD_META:
            out.append("^" + character)
        elif character == "%":
            out.append("%%")
        else:
            out.append(character)
    return "".join(out)


def cmd_line(argv: Sequence[str]) -> str:
    """把 argv 拼成一行交给 `cmd.exe /d /s /c`（每个参数单独转义）。"""
    return " ".join(cmd_quote(str(item)) for item in argv)


def invocation_argv(
    command: str,
    args: Sequence[str] = (),
    *,
    os_name: str | None = None,
    environ: Mapping[str, str] | None = None,
    which: Callable[[str], str | None] = shutil.which,
    is_file: Callable[[str], bool] | None = None,
) -> list[str]:
    """命令 + 参数 → 可执行的 argv（Windows 的 `.cmd`/`.bat`/`.ps1` 包壳在这里被正确包裹）。

    `os_name` / `environ` / `which` / `is_file` 都可注入：这条逻辑的分支只能在 Windows 上跑到，
    但我们要求它在 Linux 上也能被用例钉住（否则又要靠"用户机器上才发现"）。
    """
    platform = os_name if os_name is not None else os.name
    env = environ if environ is not None else dict(os.environ)
    exists = is_file or os.path.isfile
    resolved = resolve_command(
        command, which=which, environ=env, is_file=exists, platform=platform
    ) or (command or "").strip()
    argv = [resolved, *args]
    lowered = resolved.lower()

    # 解析包壳这一步**不按平台门控**，而是按"解析出来的路径长什么样"：
    # `.cmd`/`.bat`/`.ps1` 本来就是 Windows 专有格式，任何平台上遇到它们都只有这一种处理方式；
    # 而且这么写之后这段逻辑在本机（Linux）也能被用例钉住，不必等到用户机器上才发现。
    if lowered.endswith(_WINDOWS_SHELL_EXTS):
        direct = direct_shim_argv(resolved, args, which=which, is_file=exists)
        if direct is not None:
            return direct
        if platform != "nt":
            return argv          # 非 Windows：没有 cmd.exe，原样返回（调用方会如实报错）
        # 读不出包壳内容（不是 npm 形状）：交给 cmd 自己的解释器，参数逐个转义
        return [env.get("COMSPEC") or "cmd.exe", "/d", "/s", "/c", cmd_line(argv)]
    if lowered.endswith(_WINDOWS_PS_EXTS) and platform == "nt":
        return [
            "powershell.exe",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            *argv,
        ]
    return argv
