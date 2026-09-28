"""环境探测（规格 §9）。平台分支集中在本模块，缺失项汇总为中文可执行指引。"""

from __future__ import annotations

import errno
import os
import re
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from ..envpath import find_executable
from ..procflags import no_window_kwargs
from ..types import DetectedTool, DetectionReport

ToolName = str  # "opencode" | "bash" | "shellcheck"

_VERSION_PATTERNS: dict[str, re.Pattern[str]] = {
    "shellcheck": re.compile(r"version:\s*([0-9][^\s]*)"),
    "bash": re.compile(r"version\s+([0-9][^\s(]*)"),
    "opencode": re.compile(r"([0-9]+\.[0-9]+\.[0-9]+)"),
}

_INSTALL_HINTS = {
    "opencode": (
        "未找到 opencode：请安装 Windows 原生 opencode"
        "（choco install opencode / scoop install opencode / npm i -g opencode-ai），"
        "本应用不支持仅存在于 WSL 的安装"
    ),
    "bash": "未找到 Git Bash：请安装 Git for Windows（提供 bash.exe）",
}

# shellcheck 的缺装提示**按平台分开**：Windows 上要 shellcheck.exe，而仓库里随附的
# `tools/shellcheck` 是 Linux 可执行文件（Windows 上启动会得到 WinError 193）——
# 这条正是用户踩过的坑，所以两边都说清楚各自该装什么。
_SHELLCHECK_HINT_WINDOWS = (
    "未找到 shellcheck：Windows 上要 shellcheck.exe —— "
    "winget install --id koalaman.shellcheck，或从官方 release 下 **Windows 版 zip**"
    "（仓库里随附的 tools/shellcheck 是 **Linux 可执行文件**，Windows 上启动不了）"
)
_SHELLCHECK_HINT_POSIX = (
    "未找到 shellcheck：装一个即可 —— apt install shellcheck / dnf install ShellCheck / "
    "pacman -S shellcheck；也可以把仓库里的 tools/shellcheck 填进"
    "「设置 → 组件路径 → shellcheck」"
)


def shellcheck_install_hint(platform: str) -> str:
    return _SHELLCHECK_HINT_WINDOWS if platform == "win32" else _SHELLCHECK_HINT_POSIX

MIN_OPENCODE_VERSION = "1.1.1"

# `opencode auth list` 的最后一行是 "N credentials"（前面还有一行带路径的框）。
# 输出里带 ANSI 颜色码（实测 `\x1b[0m`），所以先剥掉再匹配，别让颜色把正则挡了。
_ANSI = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")
_AUTH_COUNT = re.compile(r"(\d+)\s+credentials?\b")

_AUTH_HINT = (
    "opencode 里没有已保存的凭据（`opencode auth list` 显示 0 credentials）："
    "如果你没有用环境变量提供 API key，生成脚本这一步会失败 —— 先跑一次 `opencode auth login`。"
    "（仅靠免费额度时，上游会拒绝「把工具全部 deny 的 agent」，而禁用工具正是本应用安全模型的前提）"
)


def parse_auth_count(output: str) -> int | None:
    """从 `opencode auth list` 的输出里取凭据条数；判断不了就返回 None。

    返回 None 与返回 0 是两件事：None 表示"输出格式不是我们认识的"（版本变了），
    此时**不能**当成"没登录"去报警 —— 那是拿一个猜测去吓用户。
    """
    match = _AUTH_COUNT.search(_ANSI.sub("", output))
    return int(match.group(1)) if match else None


@dataclass(slots=True)
class DetectDeps:
    platform: str
    exists: Callable[[str], bool]
    which: Callable[[str], str | None]
    run_version: Callable[[str], str]
    overrides: dict[str, str] = field(default_factory=dict)
    # 可选：查 opencode 的凭据。没提供就不查（"没这个能力"而不是"查了没问题"），
    # 于是所有只用 run_version 造出来的测试依赖都不受影响。
    run_auth: Callable[[str], str] | None = None
    # 可选：一次调用同时拿到 (版本输出, 起不来的原因)。给定时优先用它（生产实现见
    # `system_deps`），这样"文件在、但启动失败"能被如实区分出来；不给就退回 `run_version`。
    version_probe: Callable[[str], tuple[str, str]] | None = None


def app_base_dir() -> Path:
    r"""程序"自己那一份"东西放在哪：打包产物是 exe 旁边，源码检出是仓库根。

    为什么需要它：`packaging/build.md` 里一直写着"把 shellcheck.exe 放到 `tools\` 下"，
    但探测从来不去那儿找 —— 用户照着做也等于没做，必须手填「组件路径」。这里补上这条路。
    """
    if getattr(sys, "frozen", False):        # PyInstaller 产物：exe 就在根目录
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[2]


def candidate_paths(platform: str, tool: str) -> list[str]:
    """Windows 上的固定候选位置（Git for Windows 的安装路径、程序旁边的 shellcheck.exe 等）。

    只在 Windows 上给候选：Linux 上这些工具由 PATH / 包管理器提供，随附的
    `tools/shellcheck` 是"仓库里的开发用具"（用例用它），不改变 Linux 上原有的解析顺序。
    """
    if platform != "win32":
        return []
    if tool == "bash":
        local_appdata = os.environ.get("LOCALAPPDATA", "")
        paths = [
            "C:/Program Files/Git/bin/bash.exe",
            "C:/Program Files (x86)/Git/bin/bash.exe",
        ]
        if local_appdata:
            # Git for Windows 默认就是"仅为我安装"：装在 %LOCALAPPDATA%\Programs\Git 下，
            # 上面那两个 Program Files 路径反而没有 —— 老版本只找了 Program Files。
            # 分隔符统一成正斜杠：与上面写死的那两条一个样式，Windows 两种都认
            prefix = local_appdata.replace("\\", "/").rstrip("/")
            paths.append(f"{prefix}/Programs/Git/bin/bash.exe")
        return paths
    if tool == "opencode":
        appdata = os.environ.get("APPDATA", "")
        if not appdata:
            return []
        return [
            os.path.join(appdata, "npm", "opencode.cmd"),
            os.path.join(appdata, "npm", "opencode"),
        ]
    if tool == "shellcheck":
        # 程序旁边（打包产物），以及仓库检出里的 tools/（Windows 版名字带 .exe）
        base = str(app_base_dir()).replace("\\", "/").rstrip("/")
        return [f"{base}/tools/shellcheck.exe", f"{base}/shellcheck.exe"]
    return []


def wsl_bash_reason(platform: str, path: str) -> str:
    r"""这个 bash 是不是 WSL 的？是就给一句"为什么不能拿它跑脚本"。

    WSL 的 bash 在**探测**上完全正常（`bash --version` 跑得通），但本应用执行脚本时给的是
    **Windows 路径**（`C:\...\attempts\1\script.sh`，`cwd` 也是 Windows 路径），
    在 WSL 里这些都不成立（要写成 `/mnt/c/...`）。所以它属于"看着装了、其实不能用"，
    必须当场说清楚 —— 否则用户会看到一堆与脚本内容无关的怪错。
    """
    if platform != "win32":
        return ""
    normalized = str(path or "").replace("/", "\\").lower()
    if not normalized.endswith("bash.exe"):
        return ""
    system_root = os.environ.get("SystemRoot", "C:\\Windows").replace("/", "\\").lower()
    if f"{system_root}\\system32\\" in normalized:
        return (
            "这是 WSL 的 bash（%SystemRoot%\\System32\\bash.exe）：本应用执行脚本用的是"
            "**Windows 路径**（脚本就在运行目录里），在 WSL 里要写成 /mnt/c/... 才成立。"
            "请安装 Git for Windows（提供 Git Bash 的 bash.exe）；如果它装在非默认位置，"
            "在「设置 → 组件路径 → bash」里填它的完整路径。"
        )
    return ""


def start_failure_reason(error: BaseException) -> str:
    """把"起不来"的底层异常翻成一句能指导行动的话。

    最常见的一条：Windows 上启动 Linux 可执行文件 →
    `[WinError 193] %1 不是有效的 Win32 应用程序`（用户实测的"windows 下调不起来 shellcheck"
    就是这条：他把仓库里随附的 Linux 版 shellcheck 填进了组件路径）。
    """
    winerror = getattr(error, "winerror", None)
    number = getattr(error, "errno", None)
    text = str(error)
    if winerror == 193 or number == errno.ENOEXEC or "not a valid Win32" in text:
        # 具体该装什么由调用方补（同一个原因可能出在 bash 上，也可能出在 shellcheck 上）
        return (
            "这不是当前系统能运行的程序：多半是拿错了平台的版本"
            "（例如把仓库里随附的 Linux 版二进制用在了 Windows 上，或反过来）"
        )
    if winerror == 5 or number in (errno.EACCES, errno.EPERM):
        return "没有执行权限，或被安全软件 / 公司策略拦下"
    if number == errno.ENOENT or winerror == 2:
        return "文件不在了（可能被移动、删除，或路径写错了）"
    return text or type(error).__name__


def parse_version(tool: str, output: str) -> str:
    pattern = _VERSION_PATTERNS.get(tool)
    if pattern is None:
        return "unknown"
    match = pattern.search(output)
    return match.group(1) if match else "unknown"


def is_at_least(version: str, minimum: str) -> bool:
    def parts(value: str) -> list[int]:
        out: list[int] = []
        for chunk in value.split(".")[:3]:
            digits = re.match(r"\d+", chunk)
            out.append(int(digits.group(0)) if digits else 0)
        return out + [0] * (3 - len(out))

    left, right = parts(version), parts(minimum)
    for a, b in zip(left, right):
        if a != b:
            return a > b
    return True


def _probe_tool(tool: str, path: str, deps: DetectDeps) -> tuple[str, str]:
    """试跑一次拿到 (版本, 起不来的原因)。

    `version_probe` 给定时一次调用同时拿到输出与失败原因（生产实现用它，见 `system_deps`）；
    没给就退回 `run_version`（老调用点与老用例不用改），此时"起不来"与"输出为空"没法区分。
    """
    if deps.version_probe is not None:
        output, error = deps.version_probe(path)
    else:
        output, error = deps.run_version(path), ""
    return parse_version(tool, output), error


def resolve_tool(tool: str, deps: DetectDeps) -> DetectedTool | None:
    """探测单个组件：override → Windows 固定候选 → PATH（合并注册表 PATH，见 envpath）。

    **找到 ≠ 能用**：这里会真的试跑一次（`--version`），起不来就把原因记进
    `DetectedTool.error`。把"文件在但起不来"如实带出去，比在运行时抛一句原始 OSError 强 ——
    用户实测的"windows 下调不起来 shellcheck"就是这种（组件路径填了 Linux 版二进制）。
    """
    candidates: list[str] = []
    override = deps.overrides.get(tool)
    if override:
        candidates.append(override)
    candidates.extend(candidate_paths(deps.platform, tool))
    from_path = deps.which(tool)
    if from_path:
        candidates.append(from_path)

    for path in candidates:
        if not deps.exists(path):
            continue
        if tool == "bash" and wsl_bash_reason(deps.platform, path):
            # WSL 的 bash 能跑起来，但跑不了我们的脚本（路径形态不同），当作"起不来"处理
            return DetectedTool(
                path=path, version="unknown", error=wsl_bash_reason(deps.platform, path)
            )
        version, error = _probe_tool(tool, path, deps)
        return DetectedTool(path=path, version=version, error=error)
    return None


def _broken_tool_problem(label: str, tool: DetectedTool, advice: str = "") -> str:
    """"找到了但起不来"的那句问题：**带上路径与原因**，必要时补一句该换哪个版本。"""
    tail = f"（{advice}）" if advice else ""
    return f"{label} 启动不了（{tool.path}）：{tool.error}{tail}"


def detect_shell_deps(
    deps: DetectDeps,
) -> tuple[DetectedTool | None, DetectedTool | None, tuple[str, ...]]:
    """探测**执行侧**的两件套（bash + shellcheck），返回 (bash, shellcheck, 问题列表)。

    单独拆出来是因为依赖集合随 agent 后端而变：opencode 那条路三件套缺一不可，而换成命令行
    agent 之后 opencode 不再是依赖，bash 与 shellcheck 却仍然是（引擎执行脚本用的是它们，
    换 agent 不换执行者）。拆一份共用实现，比在两个调用点各写一遍"缺了就报什么"稳。
    问题列表的顺序与 `detect_all` 里完全一致（bash 在前、shellcheck 在后）。

    "没找到"与"找到了但起不来"是**两句不同的话**：前者让人去装，后者要指出这个文件为什么
    不能用（多半是版本下错了）。混成一句会把用户送去重装一个他已经有的东西。
    """
    bash = resolve_tool("bash", deps)
    shellcheck = resolve_tool("shellcheck", deps)
    problems: list[str] = []
    if bash is None:
        problems.append(_INSTALL_HINTS["bash"])
    elif bash.error:
        # WSL 那条原因里已经写清了"要装 Git for Windows、装在哪"，不再复述一遍
        advice = (
            ""
            if wsl_bash_reason(deps.platform, bash.path)
            else "本应用执行脚本要 Git for Windows 的 bash.exe（Windows）或系统的 bash（Linux）"
        )
        problems.append(_broken_tool_problem("bash", bash, advice))
    if shellcheck is None:
        problems.append(shellcheck_install_hint(deps.platform))
    elif shellcheck.error:
        # 补一句"该换哪个版本"，但**不复述原因里已经说过的话**（原因由 start_failure_reason 给）
        advice = (
            "Windows 要用 shellcheck.exe：winget install --id koalaman.shellcheck"
            if deps.platform == "win32"
            else "Linux 用 apt/dnf/pacman 装一个 shellcheck 即可"
        )
        problems.append(_broken_tool_problem("shellcheck", shellcheck, advice))
    return bash, shellcheck, tuple(problems)


def detect_all(deps: DetectDeps) -> DetectionReport:
    opencode = resolve_tool("opencode", deps)
    bash, shellcheck, shell_problems = detect_shell_deps(deps)

    problems: list[str] = []
    if opencode is None:
        problems.append(_INSTALL_HINTS["opencode"])
    elif opencode.version == "unknown":
        # 不能落进「版本过低」分支：那是「装了但版本解析不出来」，误报会让用户去升级一个没问题的安装。
        # 同时也不静默放行——版本未知就无法确认它支持 permission 配置，而那是安全模型的前提。
        problems.append(
            f"无法识别 opencode 版本（--version 输出解析不出）：无法确认它支持 permission 配置，"
            f"请确认版本 >= {MIN_OPENCODE_VERSION}"
        )
    elif not is_at_least(opencode.version, MIN_OPENCODE_VERSION):
        problems.append(
            f"opencode 版本过低（{opencode.version}）：需要 >= {MIN_OPENCODE_VERSION} 才有 permission 配置"
        )
    problems.extend(shell_problems)

    warnings: list[str] = []
    if opencode is not None and deps.run_auth is not None:
        # 只在**明确读到 0 条凭据**时提示：读不出来（版本换了输出格式）就不说，
        # 免得把"我不知道"说成"你没登录"。
        if parse_auth_count(deps.run_auth(opencode.path)) == 0:
            warnings.append(_AUTH_HINT)

    return DetectionReport(
        opencode=opencode,
        bash=bash,
        shellcheck=shellcheck,
        problems=tuple(problems),
        warnings=tuple(warnings),
    )


def system_deps(overrides: dict[str, str] | None = None, *, platform: str | None = None) -> DetectDeps:
    """生产环境的依赖实现：走 PATH 与真实进程。

    `platform` 可注入（用例在 Linux 上钉 Windows 分支时用）；不传就按 `os.name` 判。
    """

    def run_version(path: str) -> str:
        return run_version_detail(path)[0]

    def run_version_detail(path: str) -> tuple[str, str]:
        """试跑 `--version`：返回 (输出, 起不来的原因)。原因非空 = 这个文件根本跑不起来。

        版本探测必须与本地化无关：中文 locale 下 `bash --version` 会输出
        「GNU bash，版本 5.3.15」，规格里的英文正则就解析不出来（返回 unknown）。
        """
        env = {**os.environ, "LC_ALL": "C", "LANG": "C"}
        try:
            completed = subprocess.run(
                [path, "--version"],
                capture_output=True,
                text=True,
                timeout=20,
                env=env,
                **no_window_kwargs(),
            )
            return f"{completed.stdout}\n{completed.stderr}", ""
        except OSError as error:
            # 重点：**不要**把这条吞成"版本解析不出来"。Windows 上启动 Linux 可执行文件时
            # 抛的就是这里（WinError 193），它是"这个文件用错了平台"，不是"输出格式不认识"。
            return "", start_failure_reason(error)
        except subprocess.SubprocessError as error:
            return "", f"试运行时出错：{error}"

    def run_auth(path: str) -> str:
        env = {**os.environ, "LC_ALL": "C", "LANG": "C", "NO_COLOR": "1"}
        try:
            completed = subprocess.run(
                [path, "auth", "list"],
                capture_output=True,
                text=True,
                timeout=20,
                env=env,
                **no_window_kwargs(),
            )
            return f"{completed.stdout}\n{completed.stderr}"
        except (OSError, subprocess.SubprocessError):
            return ""

    resolved_platform = platform or ("win32" if os.name == "nt" else "linux")
    return DetectDeps(
        platform=resolved_platform,
        exists=lambda path: os.path.isfile(path),
        # 用 envpath 的实现而不是 `shutil.which`：Windows 上进程 PATH 可能是旧的
        # （改完 PATH 没重启 explorer），而且 `which` 依赖 PATHEXT 才认 `.cmd` ——
        # 用户实测"环境变量里明明有，就是获取不到"就是这两条。
        which=lambda name: find_executable(name, platform=resolved_platform),
        run_version=run_version,
        overrides=dict(overrides or {}),
        run_auth=run_auth,
        version_probe=run_version_detail,
    )
