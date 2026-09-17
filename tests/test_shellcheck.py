import pytest

from tu_shell_agent.shell_toolchain.shellcheck import (
    ShellcheckError,
    build_env,
    parse_json1,
    run_shellcheck,
)


def test_parse_json1_maps_comments():
    raw = (
        '{"comments": [{"file": "a.sh", "line": 3, "column": 7, '
        '"level": "warning", "code": 2086, "message": "Double quote"}]}'
    )
    findings = parse_json1(raw)
    assert len(findings) == 1
    assert findings[0].code == "SC2086"
    assert findings[0].line == 3
    assert findings[0].level == "warning"


def test_parse_json1_empty_comments():
    assert parse_json1('{"comments": []}') == []


def test_build_env_drops_shellcheck_opts():
    env = build_env({"PATH": "/usr/bin", "SHELLCHECK_OPTS": "--exclude=SC2086"})
    assert "SHELLCHECK_OPTS" not in env
    assert env["PATH"] == "/usr/bin"


def test_clean_script_exits_zero(tmp_path, shellcheck_path):
    script = tmp_path / "ok.sh"
    script.write_text('#!/usr/bin/env bash\nset -euo pipefail\necho "hi"\n', encoding="utf-8")
    findings, exit_code, _raw = run_shellcheck(shellcheck_path, str(script))
    assert exit_code == 0
    assert findings == []


def test_unquoted_variable_reports_sc2086_and_exit_code_one(tmp_path, shellcheck_path):
    script = tmp_path / "bad.sh"
    script.write_text('#!/usr/bin/env bash\nf="a b"\nls $f\n', encoding="utf-8")
    findings, exit_code, _raw = run_shellcheck(shellcheck_path, str(script))
    assert exit_code == 1  # 1 表示「有问题」，不是失败
    assert any(f.code == "SC2086" for f in findings)


def test_syntax_error_reports_error_level(tmp_path, shellcheck_path):
    script = tmp_path / "syntax.sh"
    script.write_text("#!/usr/bin/env bash\nif [ 1 -eq 1 ]; then\n  echo hi\n", encoding="utf-8")
    findings, _exit_code, _raw = run_shellcheck(shellcheck_path, str(script))
    assert any(f.level == "error" for f in findings)


def test_missing_file_raises_dependency_error(tmp_path, shellcheck_path):
    with pytest.raises(ShellcheckError) as excinfo:
        run_shellcheck(shellcheck_path, str(tmp_path / "nope.sh"))
    assert excinfo.value.exit_code == 2
    assert "shellcheck" in str(excinfo.value)


def test_bad_flag_raises_with_full_command(tmp_path, shellcheck_path):
    script = tmp_path / "ok.sh"
    script.write_text("#!/usr/bin/env bash\necho hi\n", encoding="utf-8")
    with pytest.raises(ShellcheckError) as excinfo:
        run_shellcheck(shellcheck_path, str(script), extra_args=["--bogus-flag"])
    assert excinfo.value.exit_code in (2, 3, 4)
    assert "--bogus-flag" in str(excinfo.value)
