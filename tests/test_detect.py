from tu_shell_agent.shell_toolchain.detect import (
    DetectDeps,
    candidate_paths,
    detect_all,
    is_at_least,
    parse_version,
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
