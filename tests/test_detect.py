import shutil

import pytest

from tu_shell_agent.shell_toolchain.detect import (
    DetectDeps,
    candidate_paths,
    detect_all,
    is_at_least,
    parse_auth_count,
    parse_version,
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
