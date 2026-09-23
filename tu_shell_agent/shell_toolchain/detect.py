"""环境探测（规格 §9）。平台分支集中在本模块，缺失项汇总为中文可执行指引。"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from dataclasses import dataclass, field
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
    "shellcheck": "未找到 shellcheck：winget install --id koalaman.shellcheck，或使用官方 release zip",
}

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


def candidate_paths(platform: str, tool: str) -> list[str]:
    """Windows 上的固定候选位置（Git for Windows 的两种安装路径等）。"""
    if platform != "win32":
        return []
    if tool == "bash":
        return [
            "C:/Program Files/Git/bin/bash.exe",
            "C:/Program Files (x86)/Git/bin/bash.exe",
        ]
    if tool == "opencode":
        appdata = os.environ.get("APPDATA", "")
        if not appdata:
            return []
        return [
            os.path.join(appdata, "npm", "opencode.cmd"),
            os.path.join(appdata, "npm", "opencode"),
        ]
    return []


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


def resolve_tool(tool: str, deps: DetectDeps) -> DetectedTool | None:
    """探测单个组件：override → Windows 固定候选 → PATH（合并注册表 PATH，见 envpath）。"""
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
        return DetectedTool(path=path, version=parse_version(tool, deps.run_version(path)))
    return None


def detect_shell_deps(
    deps: DetectDeps,
) -> tuple[DetectedTool | None, DetectedTool | None, tuple[str, ...]]:
    """探测**执行侧**的两件套（bash + shellcheck），返回 (bash, shellcheck, 问题列表)。

    单独拆出来是因为依赖集合随 agent 后端而变：opencode 那条路三件套缺一不可，而换成命令行
    agent 之后 opencode 不再是依赖，bash 与 shellcheck 却仍然是（引擎执行脚本用的是它们，
    换 agent 不换执行者）。拆一份共用实现，比在两个调用点各写一遍"缺了就报什么"稳。
    问题列表的顺序与 `detect_all` 里完全一致（bash 在前、shellcheck 在后）。
    """
    bash = resolve_tool("bash", deps)
    shellcheck = resolve_tool("shellcheck", deps)
    problems: list[str] = []
    if bash is None:
        problems.append(_INSTALL_HINTS["bash"])
    if shellcheck is None:
        problems.append(_INSTALL_HINTS["shellcheck"])
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
        # 版本探测必须与本地化无关：中文 locale 下 `bash --version` 会输出
        # 「GNU bash，版本 5.3.15」，规格里的英文正则就解析不出来（返回 unknown）。
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
            return f"{completed.stdout}\n{completed.stderr}"
        except (OSError, subprocess.SubprocessError):
            return ""

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
    )
