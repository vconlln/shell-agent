"""环境探测（规格 §9）。平台分支集中在本模块，缺失项汇总为中文可执行指引。"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from dataclasses import dataclass, field
from typing import Callable

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


@dataclass(slots=True)
class DetectDeps:
    platform: str
    exists: Callable[[str], bool]
    which: Callable[[str], str | None]
    run_version: Callable[[str], str]
    overrides: dict[str, str] = field(default_factory=dict)


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


def _resolve(tool: str, deps: DetectDeps) -> DetectedTool | None:
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


def detect_all(deps: DetectDeps) -> DetectionReport:
    opencode = _resolve("opencode", deps)
    bash = _resolve("bash", deps)
    shellcheck = _resolve("shellcheck", deps)

    problems: list[str] = []
    if opencode is None:
        problems.append(_INSTALL_HINTS["opencode"])
    elif not is_at_least(opencode.version, MIN_OPENCODE_VERSION):
        problems.append(
            f"opencode 版本过低（{opencode.version}）：需要 >= {MIN_OPENCODE_VERSION} 才有 permission 配置"
        )
    if bash is None:
        problems.append(_INSTALL_HINTS["bash"])
    if shellcheck is None:
        problems.append(_INSTALL_HINTS["shellcheck"])

    return DetectionReport(
        opencode=opencode,
        bash=bash,
        shellcheck=shellcheck,
        problems=tuple(problems),
    )


def system_deps(overrides: dict[str, str] | None = None) -> DetectDeps:
    """生产环境的依赖实现：走 PATH 与真实进程。"""

    def run_version(path: str) -> str:
        try:
            completed = subprocess.run(
                [path, "--version"], capture_output=True, text=True, timeout=20
            )
            return f"{completed.stdout}\n{completed.stderr}"
        except (OSError, subprocess.SubprocessError):
            return ""

    return DetectDeps(
        platform="win32" if os.name == "nt" else "linux",
        exists=lambda path: os.path.isfile(path),
        which=lambda name: shutil.which(name),
        run_version=run_version,
        overrides=dict(overrides or {}),
    )
