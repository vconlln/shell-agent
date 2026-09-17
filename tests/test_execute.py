import os
import threading
import time

import pytest

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


def _wait_pid_gone(pid: int, timeout_s: float = 3.0) -> bool:
    """进程消失返回 True。POSIX 用 kill(pid, 0) 探活；Windows 语义不同，调用方自行 skip。"""
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return True
        time.sleep(0.05)
    return False


@pytest.mark.skipif(os.name == "nt", reason="os.kill(pid, 0) 探活在 Windows 上语义不同；taskkill /T 由手测清单覆盖")
def test_timeout_kills_the_whole_process_tree(tmp_path, bash_path):
    """超时必须杀掉整棵进程树：留下孤儿进程正是这条安全边界的失效模式。

    只断言 timed_out / cancelled 远远不够（实测变异结果）：把 killpg 换成 os.kill 后，
    取消路径的旧用例**完全通过**、孤儿逃逸无人发现；超时路径的旧用例虽会失败，
    却是撞在那条 5 秒耗时限上（孤儿仍持有 stdout 管道写端，两个泵线程各空等
    join(timeout=5)），与进程树毫无关系。
    """
    marker = tmp_path / "child.pid"
    script = tmp_path / "tree.sh"
    script.write_text(f"sleep 300 &\necho $! > {marker}\nwait\n", encoding="utf-8")

    result = run_script(bash_path, str(script), cwd=str(tmp_path), timeout_ms=800)

    assert result.timed_out is True
    child = int(marker.read_text(encoding="utf-8").strip())
    assert _wait_pid_gone(child) is True


@pytest.mark.skipif(os.name == "nt", reason="同 timeout 用例：Windows 由手测清单覆盖")
def test_cancel_kills_the_whole_process_tree(tmp_path, bash_path):
    marker = tmp_path / "child.pid"
    script = tmp_path / "tree.sh"
    script.write_text(f"sleep 300 &\necho $! > {marker}\nwait\n", encoding="utf-8")
    cancel = threading.Event()
    threading.Timer(0.4, cancel.set).start()

    result = run_script(
        bash_path, str(script), cwd=str(tmp_path), timeout_ms=30_000, cancel=cancel
    )

    assert result.cancelled is True
    child = int(marker.read_text(encoding="utf-8").strip())
    assert _wait_pid_gone(child) is True
