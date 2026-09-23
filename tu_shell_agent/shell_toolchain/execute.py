"""用 bash 执行生成的脚本：流式回传、超时、可取消、杀进程树（规格 §11）。"""

from __future__ import annotations

import os
import signal
import subprocess
import threading
import time
from typing import Any, Callable

from ..procflags import no_window_kwargs
from ..types import ExecuteResult


def kill_tree(pid: int) -> None:
    """Windows 用 taskkill 杀整棵树；类 Unix 用进程组。"""
    if os.name == "nt":
        subprocess.run(
            ["taskkill", "/PID", str(pid), "/T", "/F"],
            capture_output=True,
            check=False,
            # 窗口程序里起子进程会弹控制台窗口（用户实测"闪终端"）——见 procflags
            **no_window_kwargs(),
        )
        return
    try:
        os.killpg(os.getpgid(pid), signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        try:
            os.kill(pid, signal.SIGKILL)
        except OSError:
            pass


def run_script(
    bash_path: str,
    script_path: str,
    cwd: str,
    timeout_ms: int,
    cancel: Any = None,
    on_stdout: Callable[[str], None] | None = None,
    on_stderr: Callable[[str], None] | None = None,
) -> ExecuteResult:
    started = time.monotonic()
    popen_kwargs: dict[str, Any] = {
        "cwd": cwd,
        "stdout": subprocess.PIPE,
        "stderr": subprocess.PIPE,
        "text": True,
        "encoding": "utf-8",
        "errors": "replace",
        "env": dict(os.environ),
    }
    if os.name == "nt":
        # 新进程组（好按整棵树杀）+ **不弹控制台窗口**（用户实测"闪终端"）
        popen_kwargs.update(no_window_kwargs(new_process_group=True))
    else:
        # start_new_session 是必需的：少了它，os.getpgid(bash) 返回的就是调用方自己的进程组，
        # 超时/取消触发的 killpg 会把 CLI/应用自己一起 SIGKILL（实测变异验证：pytest 自身被 137 杀掉）。
        popen_kwargs["start_new_session"] = True

    process = subprocess.Popen([bash_path, "--noprofile", "--norc", script_path], **popen_kwargs)

    stdout_parts: list[str] = []
    stderr_parts: list[str] = []
    state = {"timed_out": False, "cancelled": False}
    lock = threading.Lock()

    def pump(stream, sink: list[str], callback: Callable[[str], None] | None) -> None:
        if stream is None:
            return
        for chunk in iter(stream.readline, ""):
            with lock:
                sink.append(chunk)
            if callback is not None:
                callback(chunk)

    threads = [
        threading.Thread(target=pump, args=(process.stdout, stdout_parts, on_stdout), daemon=True),
        threading.Thread(target=pump, args=(process.stderr, stderr_parts, on_stderr), daemon=True),
    ]
    for thread in threads:
        thread.start()

    deadline = started + timeout_ms / 1000.0
    while process.poll() is None:
        if process.pid is not None and cancel is not None:
            is_set = getattr(cancel, "is_set", None)
            if callable(is_set) and is_set():
                state["cancelled"] = True
                kill_tree(process.pid)
                break
        if time.monotonic() > deadline:
            state["timed_out"] = True
            if process.pid is not None:
                kill_tree(process.pid)
            break
        time.sleep(0.05)

    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        if process.pid is not None:
            kill_tree(process.pid)

    for thread in threads:
        thread.join(timeout=5)

    # 注意：signal 恒为 None，被 SIGKILL 杀死的脚本表现为 exit_code=-9。
    # 调用方要区分「脚本自己退出」与「超时/取消被杀」，必须结合 timed_out / cancelled，不能只看 exit_code。
    return ExecuteResult(
        exit_code=process.returncode,
        signal=None,
        timed_out=state["timed_out"],
        cancelled=state["cancelled"],
        duration_ms=int((time.monotonic() - started) * 1000),
        stdout="".join(stdout_parts),
        stderr="".join(stderr_parts),
    )
