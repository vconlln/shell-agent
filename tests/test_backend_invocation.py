"""命令 → 可执行 argv 的转换（`agent_backends/invocation.py`）。

这一层专门处理用户实测撞到的那个坑：Windows 上 npm/包管理器装的 CLI 是 `.cmd` 包壳，
`shutil.which()` 找得到，但 `CreateProcess` 不能直接执行 `.cmd` —— 直接起会报
`[WinError 193] 不是有效的 Win32 应用程序`，界面上就表现为"这个后端检测不到"。

分支只在 Windows 上跑到，所以这里**注入平台**来钉住形状（否则又只能靠用户机器发现）。
"""

from __future__ import annotations

from tu_shell_agent.agent_backends.invocation import invocation_argv, resolve_command

NPM = r"C:\Users\x\AppData\Roaming\npm\codeagent.cmd"
COMSPEC = r"C:\Windows\System32\cmd.exe"


def _which(mapping: dict[str, str]):
    return lambda name: mapping.get(name)


# ── Windows 包壳 ──────────────────────────────────────────────────────


def test_windows_cmd_shim_goes_through_cmd_exe():
    """npm 装的 `codeagent` 其实是 `codeagent.cmd`：必须经 cmd.exe /c 起。"""
    argv = invocation_argv(
        "codeagent",
        ["--version"],
        os_name="nt",
        environ={"COMSPEC": COMSPEC},
        which=_which({"codeagent": NPM}),
    )
    assert argv == [COMSPEC, "/c", NPM, "--version"]


def test_windows_bat_shim_is_wrapped_too():
    bat = r"C:\tools\codeagent.bat"
    argv = invocation_argv(
        "codeagent", ["-p"], os_name="nt", environ={}, which=_which({"codeagent": bat})
    )
    assert argv[:2] == ["cmd.exe", "/c"] and argv[2:] == [bat, "-p"]


def test_windows_powershell_script_uses_powershell():
    ps1 = r"C:\tools\codeagent.ps1"
    argv = invocation_argv(
        "codeagent", ["--version"], os_name="nt", environ={}, which=_which({"codeagent": ps1})
    )
    assert argv[:4] == ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass"]
    assert argv[-2:] == [ps1, "--version"]


def test_windows_exe_is_not_wrapped():
    exe = r"C:\tools\codeagent.exe"
    argv = invocation_argv(
        "codeagent", ["--version"], os_name="nt", environ={}, which=_which({"codeagent": exe})
    )
    assert argv == [exe, "--version"]


# ── 其它平台与边界 ────────────────────────────────────────────────────


def test_posix_command_is_passed_through():
    argv = invocation_argv(
        "codeagent", ["--version"], os_name="posix", which=_which({"codeagent": "/usr/local/bin/codeagent"})
    )
    assert argv == ["/usr/local/bin/codeagent", "--version"]


def test_absolute_path_with_separator_is_not_searched_in_path():
    """填了完整路径就直接看它在不在，别交给 which 去猜（Windows 上路径里的反斜杠也一样）。"""
    calls: list[str] = []

    def which(name: str) -> str | None:
        calls.append(name)
        return None

    assert resolve_command("/nonexistent/codeagent", which=which) is None
    assert calls == [], "含分隔符的路径不该再走 which"


def test_missing_command_still_returns_a_runnable_shape():
    """找不到命令时也给出 argv（让调用方拿到 FileNotFoundError 并如实报错），而不是崩在转换里。"""
    argv = invocation_argv("nope", ["--version"], os_name="nt", environ={}, which=_which({}))
    assert argv == ["nope", "--version"]
