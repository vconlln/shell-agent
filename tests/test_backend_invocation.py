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


def test_windows_cmd_shim_falls_back_to_cmd_exe_when_it_is_not_an_npm_shim():
    """读不出包壳内容（不是 npm 形状）时才交给 cmd.exe，并且参数逐个转义。"""
    argv = invocation_argv(
        "codeagent",
        ["--version"],
        os_name="nt",
        environ={"COMSPEC": COMSPEC},
        which=_which({"codeagent": NPM}),
        is_file=lambda _path: False,          # 这个假路径上没有真文件 → 解析不出包壳
    )
    assert argv[:4] == [COMSPEC, "/d", "/s", "/c"]
    assert argv[4] == rf"{NPM} --version"


def test_windows_bat_shim_is_wrapped_too():
    bat = r"C:\tools\codeagent.bat"
    argv = invocation_argv(
        "codeagent",
        ["-p"],
        os_name="nt",
        environ={},
        which=_which({"codeagent": bat}),
        is_file=lambda _path: False,
    )
    assert argv[:4] == ["cmd.exe", "/d", "/s", "/c"] and argv[4] == rf"{bat} -p"


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


# ── npm 包壳：直接绕开 cmd（标点与换行才安全）──────────────────────────

NPM_SHIM = """@ECHO off
GOTO start
:find_dp0
SET dp0=%~dp0
EXIT /b
:start
SETLOCAL
CALL :find_dp0

IF EXIST "%dp0%\\node.exe" (
  SET "_prog=%dp0%\\node.exe"
) ELSE (
  SET "_prog=node"
  SET PATHEXT=%PATHEXT:;.JS;=;%
)

endLocal & goto #_undefined_# 2>NUL || title %COMSPEC% & "%_prog%"  "%dp0%\\node_modules\\codeagent\\cli.js" %*
"""


def _npm_install(tmp_path) -> tuple[str, str, str]:
    """造一个和 npm 装出来的一模一样的目录：包壳 + node.exe + 真正的 cli.js。"""
    npm = tmp_path / "AppData" / "Roaming" / "npm"
    (npm / "node_modules" / "codeagent").mkdir(parents=True)
    (npm / "node_modules" / "codeagent" / "cli.js").write_text("// cli\n", encoding="utf-8")
    (npm / "node.exe").write_text("fake", encoding="utf-8")
    shim = npm / "codeagent.cmd"
    shim.write_text(NPM_SHIM, encoding="utf-8")
    return str(shim), str(npm / "node.exe"), str(npm / "node_modules" / "codeagent" / "cli.js")


def test_npm_shim_is_resolved_to_node_plus_the_real_script(tmp_path):
    """npm 包壳里就写着"用哪个 node、跑哪个 js"：读出来就能**绕开 cmd**。

    绕开的意义：cmd 会二次解析命令行 —— 参数里的 `& %` 会被它吃掉，**换行更是命令分隔符**，
    而我们的系统提示词是多行的。走 node 直调则参数原样送达。
    """
    shim, node, script = _npm_install(tmp_path)
    argv = invocation_argv(
        "codeagent",
        ["-p", "第一行\n第二行 & 100% (x)"],
        os_name="nt",
        environ={},
        which=_which({"codeagent": shim}),
    )
    assert argv[:2] == [node, script]
    assert argv[2:] == ["-p", "第一行\n第二行 & 100% (x)"], "参数必须原样送达（含换行与标点）"


def test_npm_shim_is_also_used_at_run_time_not_only_for_probing(tmp_path):
    """探测与真正跑任务必须走**同一个**转换（否则会出现"检测通过、一跑就失败"）。

    而且真正跑任务时还多一层必要性：**系统提示词是多行的**，经 cmd 传必然被当成命令分隔符
    切碎（提示词走 stdin，但 `--append-system-prompt` 的值仍然是命令行参数）。
    """
    from tu_shell_agent.agent_backends.cli_agent import CliAgentAdapter

    shim, node, script = _npm_install(tmp_path)
    adapter = CliAgentAdapter(shim)
    prompt = "第一行\n第二行 & 100% (x)"
    argv = adapter.build_argv(session_id="s-1", model="sonnet", system_prompt=prompt)

    assert argv[:2] == [node, script], f"跑任务时没有绕开 cmd：{argv[:3]}"
    assert prompt in argv, "多行系统提示词必须原样进 argv（经 cmd 会被换行切断）"


def test_windows_search_extra_dirs_when_path_lookup_fails(tmp_path):
    """GUI 进程的 PATH 可能比终端少几条：which 找不到时要去常见安装目录找。

    这正是用户"我明明装了 codeagent 却检测不到"的典型成因（双击 exe 起的进程 PATH 不同）。
    """
    npm = tmp_path / "AppData" / "Roaming" / "npm"
    npm.mkdir(parents=True)
    shim = npm / "codeagent.cmd"
    shim.write_text("@echo off\n", encoding="utf-8")

    found = resolve_command(
        "codeagent",
        which=_which({}),                       # PATH 里没有
        environ={"APPDATA": str(tmp_path / "AppData" / "Roaming")},
        platform="nt",
    )
    assert found == str(shim)


def test_windows_candidates_prefer_exe_over_shell_shims(tmp_path):
    """同一个目录里 .exe 优先于 .cmd/.bat/.ps1：原生可执行最稳。"""
    from tu_shell_agent.agent_backends.invocation import windows_candidates

    candidates = windows_candidates(
        "codeagent", environ={"APPDATA": r"C:\Users\x\AppData\Roaming"}
    )
    first_dir = [c for c in candidates if "npm" in c and "pnpm" not in c]
    assert first_dir[0].endswith("codeagent.exe")
    assert first_dir[1].endswith("codeagent.cmd")
    assert any(c.endswith("codeagent.ps1") for c in first_dir)


def test_cmd_quote_escapes_what_cmd_would_eat():
    """兜底路径（交给 cmd）也要把 cmd 的语法字符转义掉，别让参数被它吃掉。"""
    from tu_shell_agent.agent_backends.invocation import cmd_line, cmd_quote

    assert cmd_quote("plain") == "plain"
    assert cmd_quote("a & b") == '"a & b"'          # 引号内 & 是字面量
    assert cmd_quote("100%") == "100%%"             # 包壳是 .bat：%% 才是字面量 %
    assert cmd_quote("a&b") == "a^&b"               # 不加引号时要 ^ 转义
    assert cmd_quote("x>y") == "x^>y"
    assert cmd_quote("第一\n第二行") == '"第一 第二行"'  # 换行只能退化成空格（所以要优先 npm 直调）
    assert cmd_line(["t.cmd", "-p", "a&b"]) == "t.cmd -p a^&b"


# ── 版本探测：别把能用的东西判成"没装" ─────────────────────────────────


class _Runner:
    """假的 subprocess.run：按参数返回预设结果，并记录调用参数。"""

    def __init__(self, table: dict[tuple[str, ...], tuple[int, str]]) -> None:
        self.table = table
        self.calls: list[tuple[list[str], dict]] = []

    def __call__(self, argv, **kwargs):
        self.calls.append((list(argv), kwargs))
        args = tuple(argv[1:])
        returncode, output = self.table.get(args, (1, "unknown option"))

        class _Completed:
            pass

        completed = _Completed()
        completed.returncode = returncode
        completed.stdout = output
        completed.stderr = ""
        return completed


def test_probe_falls_back_to_other_version_flags(monkeypatch, tmp_path):
    """不是每个 CLI 都认 `--version`：`-v` 也要试，问出版本就算探测成功。"""
    from tu_shell_agent.agent_backends import cli_agent

    exe = tmp_path / "codeagent"
    exe.write_text("#!/bin/sh\n", encoding="utf-8")
    monkeypatch.setattr(cli_agent, "resolve_command", lambda _cmd: str(exe))
    runner = _Runner({("-v",): (0, "codeagent 1.4.2\n")})

    tool, message = cli_agent.probe_version("codeagent", ("--version",), runner=runner)

    assert message == "" and tool is not None
    assert tool.version == "1.4.2"
    assert [call[0][1:] for call in runner.calls] == [["--version"], ["-v"]]


def test_probe_accepts_a_command_that_runs_but_prints_no_version(monkeypatch, tmp_path):
    """退出码 0、只是没打版本号 → 仍然算"找到了"（版本未知）。

    用户实测"装了却检测不到"里就有这一种：CLI 能跑，但 `--version` 的输出里没有版本号，
    旧逻辑直接判失败 —— 那是把"能干活"误判成"没装"。
    """
    from tu_shell_agent.agent_backends import cli_agent

    exe = tmp_path / "codeagent"
    exe.write_text("#!/bin/sh\n", encoding="utf-8")
    monkeypatch.setattr(cli_agent, "resolve_command", lambda _cmd: str(exe))
    runner = _Runner({("--version",): (0, "usage: codeagent [options]\n")})

    tool, message = cli_agent.probe_version("codeagent", ("--version",), runner=runner)

    assert message == "" and tool is not None
    assert tool.version == "未识别"
    assert "命令可用" in cli_agent.ProbeResult(tool, message).detail()


def test_probe_gives_stdin_a_devnull(monkeypatch, tmp_path):
    """探测是无人值守的：CLI 若想读输入会挂到超时（界面上就是"点了检测没反应"）。"""
    from tu_shell_agent.agent_backends import cli_agent
    import subprocess as subprocess_module

    exe = tmp_path / "codeagent"
    exe.write_text("#!/bin/sh\n", encoding="utf-8")
    monkeypatch.setattr(cli_agent, "resolve_command", lambda _cmd: str(exe))
    runner = _Runner({("--version",): (0, "1.0.0\n")})

    cli_agent.probe_version("codeagent", ("--version",), runner=runner)

    assert runner.calls[0][1].get("stdin") is subprocess_module.DEVNULL


def test_probe_reports_every_flag_it_tried(monkeypatch, tmp_path):
    """全部失败时，把试过哪些参数写进提示 —— 用户据此能判断"是没装还是参数不对"。"""
    from tu_shell_agent.agent_backends import cli_agent

    exe = tmp_path / "codeagent"
    exe.write_text("#!/bin/sh\n", encoding="utf-8")
    monkeypatch.setattr(cli_agent, "resolve_command", lambda _cmd: str(exe))
    runner = _Runner({})

    tool, message = cli_agent.probe_version("codeagent", ("--version",), runner=runner)

    assert tool is None
    assert "--version" in message and "-v" in message and "--help" in message
    assert [call[0][1:] for call in runner.calls] == [["--version"], ["-v"], ["version"], ["--help"]]
