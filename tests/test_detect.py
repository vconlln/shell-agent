import shutil

import pytest

from tu_shell_agent.shell_toolchain.detect import (
    DetectDeps,
    candidate_paths,
    detect_all,
    is_at_least,
    parse_auth_count,
    parse_version,
    start_failure_reason,
    system_deps,
)

LINUX_VERSIONS = {
    "/usr/bin/bash": "GNU bash, version 5.2.037-1 (x86_64-pc-linux-gnu)",
    "/usr/bin/shellcheck": "ShellCheck - shell script analysis tool\nversion: 0.11.0",
    "/usr/bin/opencode": "1.18.31",
}


def test_linux_resolves_all_three_from_path():
    report = detect_all(
        DetectDeps(
            platform="linux",
            exists=lambda path: True,
            which=lambda name: {
                "bash": "/usr/bin/bash",
                "shellcheck": "/usr/bin/shellcheck",
                "opencode": "/usr/bin/opencode",
            }.get(name),
            run_version=lambda path: LINUX_VERSIONS.get(path, ""),
        )
    )
    assert report.bash.path == "/usr/bin/bash"
    assert report.shellcheck.version == "0.11.0"
    assert report.opencode.version == "1.18.31"
    assert report.problems == ()


def test_windows_finds_git_bash_by_candidate_order():
    git_bash = "C:/Program Files/Git/bin/bash.exe"
    report = detect_all(
        DetectDeps(
            platform="win32",
            exists=lambda path: path == git_bash,
            which=lambda name: None,
            run_version=lambda path: "GNU bash, version 5.2.37(1)-release",
        )
    )
    assert report.bash.path == git_bash


def test_missing_tools_produce_actionable_problems():
    report = detect_all(
        DetectDeps(platform="win32", exists=lambda path: False, which=lambda name: None, run_version=lambda path: "")
    )
    joined = "\n".join(report.problems)
    assert report.opencode is None
    assert "opencode" in joined
    assert "Git Bash" in joined
    assert "shellcheck" in joined


def test_overrides_win_over_autodetection():
    report = detect_all(
        DetectDeps(
            platform="linux",
            exists=lambda path: path == "/opt/custom/bash",
            which=lambda name: "/usr/bin/bash" if name == "bash" else None,
            run_version=lambda path: "GNU bash, version 5.2.37(1)-release",
            overrides={"bash": "/opt/custom/bash"},
        )
    )
    assert report.bash.path == "/opt/custom/bash"


def test_candidate_paths_cover_both_program_files_locations():
    paths = candidate_paths("win32", "bash")
    assert "C:/Program Files/Git/bin/bash.exe" in paths
    assert "C:/Program Files (x86)/Git/bin/bash.exe" in paths


def test_old_opencode_version_is_flagged():
    report = detect_all(
        DetectDeps(
            platform="linux",
            exists=lambda path: True,
            which=lambda name: f"/usr/bin/{name}",
            run_version=lambda path: "1.0.140" if "opencode" in path else "ShellCheck\nversion: 0.11.0",
        )
    )
    assert any("版本过低" in problem for problem in report.problems)


def test_parse_version_extracts_semver():
    assert parse_version("shellcheck", "ShellCheck\nversion: 0.11.0") == "0.11.0"
    assert parse_version("bash", "GNU bash, version 5.2.37(1)-release (x86_64)") == "5.2.37"


def test_is_at_least_compares_three_segments():
    assert is_at_least("1.18.31", "1.1.1") is True
    assert is_at_least("1.0.9", "1.1.1") is False
    assert is_at_least("1.1.1", "1.1.1") is True


def test_system_deps_probes_version_in_c_locale():
    """版本探测必须与本地化无关：中文 locale 下 `bash --version` 输出「GNU bash，版本 5.3.15」，
    英文正则会解析出 unknown。这条在中文机器上能真实抓住该缺陷。"""
    bash = shutil.which("bash")
    if bash is None:
        pytest.skip("本机没有 bash")
    output = system_deps().run_version(bash)
    assert parse_version("bash", output) != "unknown"


def test_unparseable_opencode_version_is_reported_honestly_not_as_too_old():
    """装了但版本解析不出来时，不能误报成「版本过低」，也不能静默放行。"""
    report = detect_all(
        DetectDeps(
            platform="linux",
            exists=lambda path: True,
            which=lambda name: f"/usr/bin/{name}",
            run_version=lambda path: "not-a-version" if "opencode" in path else "ShellCheck\nversion: 0.11.0",
        )
    )
    joined = "\n".join(report.problems)
    assert "版本过低" not in joined
    assert "无法识别" in joined
    assert report.opencode is not None and report.opencode.version == "unknown"


# ── opencode 凭据检查（无凭据时最可能的"自检全绿、一生成就失败"）────────────


def test_parse_auth_count_handles_real_cli_output():
    """真实的 `opencode auth list` 输出带 ANSI 颜色码，而且单复数会变。"""
    real = (
        "\x1b[0m\n"
        "┌  Credentials \x1b[90m~/.local/share/opencode/auth.json\n"
        "│\n"
        "└  0 credentials\n"
    )
    assert parse_auth_count(real) == 0
    assert parse_auth_count("└  2 credentials") == 2
    assert parse_auth_count("└  1 credential") == 1


def test_parse_auth_count_returns_none_when_it_cannot_tell():
    """读不出来必须返回 None 而不是 0。

    返回 0 会被下游当成"没登录"去提示用户 —— 那是拿一个猜测去吓人；
    版本升级换了输出格式时就会变成假警报。
    """
    assert parse_auth_count("") is None
    assert parse_auth_count("credentials: unknown") is None
    assert parse_auth_count("└  no credentials") is None


def _deps_with_auth(auth_output: str) -> DetectDeps:
    return DetectDeps(
        platform="linux",
        exists=lambda path: True,
        which=lambda name: f"/usr/bin/{name}",
        run_version=lambda path: LINUX_VERSIONS.get(path, ""),
        run_auth=lambda path: auth_output,
    )


def test_zero_credentials_produces_a_warning_not_a_problem():
    """0 凭据 → 提示（warnings），不是问题（problems）。

    为什么不能算问题：用户完全可能用环境变量提供 API key，那时 auth.json 是空的但生成照跑。
    写成 problems 会让 CLI 直接拒绝运行（假故障），而这是最常见的合法配置之一。
    """
    report = detect_all(_deps_with_auth("└  0 credentials"))

    assert report.problems == ()
    assert len(report.warnings) == 1
    assert "opencode auth login" in report.warnings[0]


def test_credentials_present_produces_no_warning():
    report = detect_all(_deps_with_auth("└  3 credentials"))
    assert report.warnings == ()


def test_unreadable_auth_output_does_not_warn():
    """输出读不出来（版本换了格式）时保持沉默：不把"我不知道"说成"你没登录"。"""
    report = detect_all(_deps_with_auth("未知格式的输出"))
    assert report.warnings == ()


def test_no_auth_capability_means_no_warning():
    """没提供 run_auth 就不查（既有测试全是这种依赖），不能凭空冒出警告。"""
    report = detect_all(
        DetectDeps(
            platform="linux",
            exists=lambda path: True,
            which=lambda name: f"/usr/bin/{name}",
            run_version=lambda path: LINUX_VERSIONS.get(path, ""),
        )
    )
    assert report.warnings == ()


# ── "找到了但起不来"：Windows 上的真实现场 ──────────────────────────────
#
# 用户实测（2026-09-20）："windows 下调不起来 shellcheck，win11 好像没法运行 linux 脚本"。
# 查下来是他把**仓库里随附的 Linux 版 shellcheck** 填进了「设置 → 组件路径」——那个文件是
# ELF 可执行文件，Windows 启动它只会得到 `[WinError 193] %1 不是有效的 Win32 应用程序`。
# 下面这几条钉住三件事：不能当成"未找到"、不能当成可用、必须把原因说出来。


def _winerror(number: int, text: str) -> OSError:
    """造一个带 winerror 的 OSError（在 Linux 上也能复现 Windows 的失败文本）。"""
    error = OSError(text)
    error.winerror = number          # type: ignore[attr-defined]
    return error


def _deps_win32(*, exists, which=None, probe=None, overrides=None) -> DetectDeps:
    return DetectDeps(
        platform="win32",
        exists=exists,
        which=which or (lambda name: None),
        run_version=lambda path: "",
        overrides=overrides or {},
        version_probe=probe,
    )


def test_linux_binary_in_component_path_is_reported_as_unrunnable():
    """Linux 版 shellcheck 填进组件路径 → 说"启动不了"并给出原因，**不是**"未找到"。"""
    linux_shellcheck = "D:/repo/tools/shellcheck"
    report = detect_all(
        _deps_win32(
            exists=lambda path: path == linux_shellcheck,
            overrides={"shellcheck": linux_shellcheck},
            probe=lambda path: (
                ("", start_failure_reason(_winerror(193, "[WinError 193] %1 不是有效的 Win32 应用程序")))
                if "shellcheck" in path
                else ("GNU bash, version 5.2.37(1)-release", "")
            ),
        )
    )
    problems = "\n".join(report.problems)

    assert report.shellcheck is not None, "找到了的文件不该被当成没找到"
    assert report.shellcheck.path == linux_shellcheck
    assert report.shellcheck.error, "起不来的原因必须记下来，否则界面上没法解释"
    assert "启动不了" in problems
    assert "Win32" in problems or "拿错了平台" in problems
    assert "winget" in problems, "要告诉他 Windows 上该装什么"
    assert "未找到 shellcheck" not in problems, "报成未找到会让人去重装一个已有的东西"


def test_missing_shellcheck_hint_is_platform_specific():
    """缺 shellcheck 的提示按平台分开说：Windows 用 winget + shellcheck.exe，Linux 用包管理器。

    以前这条提示在 Linux 上也让人去 `winget install`，等于没给建议。
    """
    missing = lambda path: False  # noqa: E731 - 故意一行，读起来更直
    windows = "\n".join(
        detect_all(_deps_win32(exists=missing)).problems
    )
    linux = "\n".join(
        detect_all(
            DetectDeps(
                platform="linux", exists=missing, which=lambda name: None,
                run_version=lambda path: "",
            )
        ).problems
    )

    assert "shellcheck.exe" in windows and "winget" in windows
    assert "winget" not in linux
    assert "apt install shellcheck" in linux
    assert "Linux" in windows, "Windows 上要说明仓库里那份是 Linux 版"


def test_wsl_bash_is_flagged_even_though_it_runs():
    """WSL 的 bash 能跑起来，但跑不了我们的脚本（脚本里是 Windows 路径）——必须当场说清。"""
    wsl = "C:/Windows/System32/bash.exe"
    report = detect_all(
        _deps_win32(
            exists=lambda path: path == wsl,
            which=lambda name: wsl if name == "bash" else None,
        )
    )
    problems = "\n".join(report.problems)

    assert report.bash is not None and report.bash.error
    assert "WSL" in problems
    assert "Git for Windows" in problems
    assert "组件路径" in problems, "要告诉他 Git Bash 装在别处时怎么指过来"


def test_real_git_bash_is_not_flagged():
    """正常的 Git Bash 不能被上面那条误伤（它在候选路径里排第一）。"""
    git_bash = "C:/Program Files/Git/bin/bash.exe"
    report = detect_all(
        _deps_win32(
            exists=lambda path: path == git_bash,
            probe=lambda path: ("GNU bash, version 5.2.37(1)-release", ""),
        )
    )

    assert report.bash is not None
    assert report.bash.error == ""
    assert not [problem for problem in report.problems if "bash" in problem]


def test_start_failure_reason_translates_the_common_cases():
    """三种最常见的启动失败各有一句对症的话（原来的实现把它们都吞成"版本解析不出来"）。"""
    assert "平台的版本" in start_failure_reason(_winerror(193, "[WinError 193]"))
    assert "权限" in start_failure_reason(PermissionError(13, "Permission denied"))
    assert "不在了" in start_failure_reason(FileNotFoundError(2, "No such file"))
    # 认不出来的异常照原样带出去，不编一句假的
    assert "怪错误" in start_failure_reason(RuntimeError("怪错误"))


def test_candidate_paths_include_git_for_windows_per_user_install(monkeypatch):
    """Git for Windows 默认是"仅为我安装"，装在 %LOCALAPPDATA%\\Programs\\Git —— 也要找。"""
    monkeypatch.setenv("LOCALAPPDATA", r"C:\Users\tester\AppData\Local")

    paths = candidate_paths("win32", "bash")

    assert any(path.endswith("/Programs/Git/bin/bash.exe") for path in paths), paths
    assert any("AppData/Local" in path for path in paths), paths


def test_candidate_paths_look_next_to_the_app_for_shellcheck():
    """`packaging/build.md` 一直写着把 shellcheck.exe 放 `tools\\` 下，代码却从没去那儿找。"""
    paths = candidate_paths("win32", "shellcheck")

    assert any(path.endswith("/tools/shellcheck.exe") for path in paths), paths
    assert any(path.endswith("/shellcheck.exe") for path in paths), paths


def test_candidate_paths_stay_empty_on_linux():
    """Linux 不改原有解析顺序：这些工具由 PATH / 包管理器提供（随附的 tools/shellcheck
    是仓库里的开发用具，用例用它，替换掉用户的 PATH 解析会是另一个话题）。"""
    assert candidate_paths("linux", "bash") == []
    assert candidate_paths("linux", "shellcheck") == []
    assert candidate_paths("linux", "opencode") == []


def test_run_shellcheck_explains_a_binary_it_cannot_start(monkeypatch):
    """跑的时候才炸的那种（文件被换掉 / 探测之后才坏）也要给一句人话，而不是原始 WinError。

    这是用户"windows 下调不起来 shellcheck"在**运行阶段**的样子：以前这里直接把 OSError
    交给上层，界面上就是一行 `[WinError 193] %1 不是有效的 Win32 应用程序`。
    """
    from tu_shell_agent.shell_toolchain import shellcheck as shellcheck_module

    def explode(*_args, **_kwargs):
        raise _winerror(193, "[WinError 193] %1 不是有效的 Win32 应用程序")

    monkeypatch.setattr(shellcheck_module.subprocess, "run", explode)

    with pytest.raises(shellcheck_module.ShellcheckError) as caught:
        shellcheck_module.run_shellcheck("D:/repo/tools/shellcheck", "/tmp/script.sh")

    message = str(caught.value)
    assert "启动不了 shellcheck" in message
    assert "D:/repo/tools/shellcheck" in message
    assert "拿错了平台的版本" in message
    assert caught.value.exit_code is None, "启动失败没有退出码，不能编一个出来"
