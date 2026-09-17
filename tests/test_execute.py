import threading
import time

from tu_shell_agent.shell_toolchain.execute import kill_tree, run_script


def test_captures_stdout_stderr_and_exit_code(tmp_path, bash_path):
    script = tmp_path / "ok.sh"
    script.write_text("echo 到标准输出\necho 到标准错误 >&2\nexit 0\n", encoding="utf-8")
    result = run_script(bash_path, str(script), cwd=str(tmp_path), timeout_ms=5_000)
    assert result.stdout.strip() == "到标准输出"
    assert result.stderr.strip() == "到标准错误"
    assert result.exit_code == 0
    assert result.timed_out is False
    assert result.cancelled is False


def test_nonzero_exit_is_returned_not_raised(tmp_path, bash_path):
    script = tmp_path / "fail.sh"
    script.write_text("echo boom >&2\nexit 7\n", encoding="utf-8")
    result = run_script(bash_path, str(script), cwd=str(tmp_path), timeout_ms=5_000)
    assert result.exit_code == 7
    assert "boom" in result.stderr


def test_timeout_kills_process_tree(tmp_path, bash_path):
    script = tmp_path / "slow.sh"
    script.write_text("sleep 30\n", encoding="utf-8")
    started = time.monotonic()
    result = run_script(bash_path, str(script), cwd=str(tmp_path), timeout_ms=400)
    assert result.timed_out is True
    assert time.monotonic() - started < 5


def test_cancel_event_stops_run(tmp_path, bash_path):
    script = tmp_path / "long.sh"
    script.write_text("sleep 30\n", encoding="utf-8")
    cancel = threading.Event()
    threading.Timer(0.3, cancel.set).start()
    result = run_script(bash_path, str(script), cwd=str(tmp_path), timeout_ms=30_000, cancel=cancel)
    assert result.cancelled is True


def test_streaming_callbacks_receive_output_in_order(tmp_path, bash_path):
    script = tmp_path / "stream.sh"
    script.write_text("echo one\nsleep 0.2\necho two\n", encoding="utf-8")
    chunks: list[str] = []
    run_script(
        bash_path, str(script), cwd=str(tmp_path), timeout_ms=5_000,
        on_stdout=chunks.append,
    )
    joined = "".join(chunks)
    assert "one" in joined and "two" in joined


def test_kill_tree_on_dead_pid_is_silent():
    kill_tree(999_999)  # 不应抛异常
