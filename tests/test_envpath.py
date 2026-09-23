"""Windows 上"从环境变量里自动获取组件路径"（`tu_shell_agent/envpath.py`）。

用户实测："你这个在 windows 上自动获取环境变量里的组件路径获取不到，windows 有环境变量，
但是仍然无法自动获取"。两个真实成因：

1. **进程的 PATH 可能是旧的**：Windows 在登录/启动时复制一份 PATH，用户在"系统属性"里刚加完目录，
   已经在跑的 explorer.exe 仍是旧环境，从它启动的 GUI（双击 exe）自然也拿不到 —— 而终端是新开的，
   所以"我明明配了"。注册表里的 `Path` 才是当前设置。
2. **`shutil.which` 依赖 `PATHEXT`**：npm/scoop/pipx 装的是 `.cmd` 包壳，没有扩展名可查时要靠
   `PATHEXT` 才知道试 `.CMD`；被上层程序/服务启动时它可能被清掉。

这段逻辑只在 Windows 上跑到，所以这里注入注册表读取器与文件系统来钉住它。
"""

from __future__ import annotations

import os
from pathlib import Path

from tu_shell_agent.envpath import (
    effective_path,
    expand_windows_vars,
    find_executable,
    registry_path_entries,
)


def _registry(values: dict[tuple[str, str], str]):
    """假的注册表读取器：按 (root, subkey) 给出 Path 值。"""

    def read(root: str, subkey: str, name: str) -> str:
        assert name == "Path"
        return values.get((root, subkey), "")

    return read


# ── 变量展开 ──────────────────────────────────────────────────────────


def test_percent_variables_are_expanded():
    """注册表里的 Path 常写成 `%SystemRoot%\\system32`：不展开等于拿到一堆死字符串。"""
    env = {"SystemRoot": r"C:\Windows", "USERPROFILE": r"C:\Users\x"}
    assert expand_windows_vars(r"%SystemRoot%\system32;%USERPROFILE%\bin", env) == (
        r"C:\Windows\system32;C:\Users\x\bin"
    )
    # 认不出来的变量原样保留（宁可留着让用户看懂，也不要展开成空串把路径拼坏）
    assert expand_windows_vars(r"%NOPE%\x", env) == r"%NOPE%\x"


# ── 合并 PATH ─────────────────────────────────────────────────────────


def test_registry_path_is_merged_after_the_process_path():
    """进程 PATH 在前、注册表里多出来的目录补在后（去重，大小写不敏感）。"""
    env = {"PATH": r"C:\stale;C:\shared"}
    read = _registry(
        {
            ("HKCU", "Environment"): r"C:\shared;%USERPROFILE%\npm",
            ("HKLM", r"SYSTEM\CurrentControlSet\Control\Session Manager\Environment"): r"C:\Windows",
        }
    )
    merged = effective_path(
        environ={**env, "USERPROFILE": r"C:\Users\x"}, read_value=read, platform="nt"
    )
    assert merged.split(";") == [r"C:\stale", r"C:\shared", r"C:\Users\x\npm", r"C:\Windows"]


def test_user_level_registry_wins_over_machine_level():
    """用户级在系统级之前：用户自己装的版本优先（与 Windows 的解析顺序一致）。"""
    read = _registry(
        {
            ("HKCU", "Environment"): r"C:\user\bin",
            ("HKLM", r"SYSTEM\CurrentControlSet\Control\Session Manager\Environment"): r"C:\machine\bin",
        }
    )
    entries = registry_path_entries(environ={}, read_value=read)
    assert entries == [r"C:\user\bin", r"C:\machine\bin"]


def test_posix_path_is_left_alone():
    """非 Windows 不动 PATH：那边的路径分隔符与注册表都不适用。"""
    assert effective_path(environ={"PATH": "/usr/bin:/bin"}, platform="posix") == "/usr/bin:/bin"


# ── "明明在 PATH 里却找不到" ───────────────────────────────────────────


def test_finds_a_cmd_shim_without_pathext(tmp_path):
    """没有 PATHEXT 也要能找到 `.cmd` 包壳（npm 装的就是它）。

    实测成因：`shutil.which("codeagent")` 在 PATHEXT 被清掉的环境里找不到 `codeagent.cmd`，
    于是"环境变量里明明有"却报找不到。
    """
    bin_dir = tmp_path / "npm"
    bin_dir.mkdir()
    shim = bin_dir / "codeagent.cmd"
    shim.write_text("@echo off\n", encoding="utf-8")

    found = find_executable(
        "codeagent",
        environ={"PATH": str(bin_dir)},       # 故意不带 PATHEXT
        platform="nt",
    )
    assert found == str(shim)


def test_extension_priority_prefers_exe(tmp_path):
    """同名时 `.exe` 优先于 `.cmd` / `.bat` / `.ps1`（原生可执行最稳）。"""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    for suffix in (".ps1", ".bat", ".cmd", ".exe"):
        (bin_dir / f"tool{suffix}").write_text("x", encoding="utf-8")
    assert find_executable("tool", environ={"PATH": str(bin_dir)}, platform="nt") == str(
        bin_dir / "tool.exe"
    )


def test_stale_process_path_is_rescued_by_the_registry(tmp_path):
    """**用户报的那条**：进程 PATH 里没有（旧环境），注册表里有 → 仍然要能找到。"""
    npm = tmp_path / "AppData" / "Roaming" / "npm"
    npm.mkdir(parents=True)
    shim = npm / "opencode.cmd"
    shim.write_text("@echo off\n", encoding="utf-8")

    read = _registry({("HKCU", "Environment"): str(npm)})
    found = find_executable(
        "opencode",
        environ={"PATH": r"C:\Windows\system32"},     # 旧环境：不含 npm 目录
        read_value=read,
        platform="nt",
    )
    assert found == str(shim)


def test_absolute_path_is_checked_directly(tmp_path):
    """已经是路径就直接看它在不在（别再去 PATH 里猜）。"""
    target = tmp_path / "tool.exe"
    target.write_text("x", encoding="utf-8")
    assert find_executable(str(target), platform="nt") == str(target)
    assert find_executable(str(tmp_path / "nope.exe"), platform="nt") is None


def test_posix_lookup_still_uses_plain_path(tmp_path):
    """非 Windows：按 `os.pathsep` 与"文件名就是文件名"来查，不要套 Windows 的后缀规则。"""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    tool = bin_dir / "tool"
    tool.write_text("#!/bin/sh\n", encoding="utf-8")
    assert find_executable("tool", environ={"PATH": str(bin_dir)}, platform="posix") == str(tool)
    assert find_executable("tool.exe", environ={"PATH": str(bin_dir)}, platform="posix") is None


# ── 探测链路真的用上了它 ───────────────────────────────────────────────


def test_detection_uses_the_merged_path_resolver(tmp_path, monkeypatch):
    """`detect_all` 走的 which 必须是这个实现（否则用户那条"获取不到"照旧）。

    `system_deps()` 的平台取自 `os.name`，所以这里把它注入成 nt —— 与 test_detect.py 的
    做法一致：Windows 分支只在 Windows 上跑到，不注入就等于没测。
    """
    from tu_shell_agent.shell_toolchain import detect as detect_module

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    shim = bin_dir / "opencode.cmd"
    shim.write_text("@echo off\n", encoding="utf-8")
    monkeypatch.setenv("PATH", str(bin_dir))
    monkeypatch.delenv("PATHEXT", raising=False)
    monkeypatch.setenv("APPDATA", str(tmp_path / "no-such-appdata"))
    # 注入平台：不能改 `os.name`（那会让 pathlib 去实例化 WindowsPath 直接抛异常），
    # 所以 system_deps 接受 platform 参数。
    report = detect_module.detect_all(detect_module.system_deps(platform="win32"))

    assert report.opencode is not None, "合并 PATH / 补扩展名那条路没生效"
    assert report.opencode.path == str(shim)


def test_settings_page_fills_empty_component_fields_only(qtbot, tmp_path):
    """「自动检测」把找到的路径填进**空栏**；用户手填过的不覆盖（那是他的选择）。"""
    from tu_shell_agent.ui.pages.settings_page import SettingsPage
    from tu_shell_agent.ui.settings import AppSettings

    page = SettingsPage()
    qtbot.addWidget(page)
    page.set_settings(
        AppSettings(run_root=str(tmp_path / "runs"), templates_dir=str(tmp_path / "tpl"))
    )
    page.bash_path_edit.setText(r"C:\my\bash.exe")          # 用户手填的

    filled = page.apply_detected_paths(
        opencode=r"C:\Users\x\AppData\Roaming\npm\opencode.cmd",
        bash=r"C:\Program Files\Git\bin\bash.exe",
        shellcheck=r"C:\tools\shellcheck.exe",
    )

    assert page.opencode_path_edit.text() == r"C:\Users\x\AppData\Roaming\npm\opencode.cmd"
    assert page.shellcheck_path_edit.text() == r"C:\tools\shellcheck.exe"
    assert page.bash_path_edit.text() == r"C:\my\bash.exe", "手填的值被覆盖了"
    assert len(filled) == 2
    assert "已自动填入" in page.components_hint.text()
