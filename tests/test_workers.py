"""后台线程托管（`ui/workers.py`）：引用被覆盖与退出时还在跑都会让 Qt abort。"""

from __future__ import annotations

import subprocess
import sys
import textwrap
import time
from pathlib import Path

from PySide6.QtCore import QThread


class _Sleeper(QThread):
    def __init__(self, seconds: float) -> None:
        super().__init__()
        self._seconds = seconds

    def run(self) -> None:  # noqa: D102 - QThread
        time.sleep(self._seconds)


def test_track_keeps_every_worker_alive(qtbot):
    """连续起两个 worker 不能把前一个丢掉。

    之前是 `self._models_worker = worker` 这样的单个字段：第二次请求一覆盖，
    还在跑的 QThread 就失去引用被 GC —— Qt 直接 abort（用户报的"选模型直接闪退"）。
    """
    from tu_shell_agent.ui import workers

    first, second = _Sleeper(0.4), _Sleeper(0.4)
    workers.track(first)
    workers.track(second)
    try:
        assert first in workers.running() and second in workers.running()
        assert first.isRunning() and second.isRunning()
    finally:
        assert workers.wait_all(5_000) == []
    # 结束后从名单里摘掉，不会越攒越多
    assert first not in workers.running() and second not in workers.running()


def test_track_is_idempotent(qtbot):
    """同一个 worker 重复 track 不该启动两次。"""
    from tu_shell_agent.ui import workers

    worker = _Sleeper(0.3)
    workers.track(worker)
    workers.track(worker)
    try:
        assert len([item for item in workers.running() if item is worker]) == 1
    finally:
        workers.wait_all(5_000)


def test_wait_all_reports_workers_that_outlive_the_timeout(qtbot):
    from tu_shell_agent.ui import workers

    worker = _Sleeper(1.5)
    workers.track(worker)
    try:
        assert workers.wait_all(50) == [worker], "超时的线程要如实报出来（而不是假装等到了）"
    finally:
        workers.wait_all(5_000)


def test_app_exits_without_aborting_while_a_worker_runs(tmp_path):
    """端到端复现用户那次闪退：**退出时线程还在跑**。

    以前进程会收到 SIGABRT（`QThread: Destroyed while thread '' is still running`，
    本机 coredump 确认过），现在退出前的最后一道闸会先等（等不到就 os._exit）。
    这条用例跑的是子进程，所以真崩了不会把测试进程带走。
    """
    script = textwrap.dedent(
        """
        import os, sys, time
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        sys.path.insert(0, %r)

        from PySide6.QtCore import QTimer
        from PySide6.QtWidgets import QApplication

        # 让"取模型列表"这一步必定比退出慢，模拟用户点完就退出的场景
        from tu_shell_agent.opencode_adapter import models
        # 睡 5s：保证子进程退出时线程**必定还在跑**（否则这条用例在"没有最后一道闸"时
        # 也会通过 —— 变异验证时踩到过）
        models.list_models = lambda *a, **k: (time.sleep(5.0), ["deepseek/x"])[1]

        from tu_shell_agent.ui.main_window import MainWindow
        from tu_shell_agent.ui.settings import AppSettings
        from tu_shell_agent.ui import workers

        app = QApplication([])
        window = MainWindow(settings=AppSettings())
        window.show()
        window.right_tabs.setCurrentWidget(window.chat_panel)   # 触发拉模型列表
        window.controller._on_models_requested()               # 再点一次（原先会覆盖引用）
        QTimer.singleShot(100, app.quit)
        app.exec()
        print("clean-exit")
        sys.stdout.flush()            # wait_or_exit 可能走 os._exit，输出必须已经刷出去
        workers.wait_or_exit(300)     # 与应用退出路径同一行（这里只等 300ms，用例才快）
        """
    ) % str(Path(__file__).resolve().parents[1])
    completed = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        timeout=120,
        env={**__import__("os").environ, "QT_QPA_PLATFORM": "offscreen"},
    )
    output = completed.stdout + completed.stderr
    assert completed.returncode == 0, f"退出时崩了（returncode={completed.returncode}）：{output}"
    assert "clean-exit" in completed.stdout
    assert "Destroyed while thread" not in output, output


def _run_script(body: str) -> tuple[int, float, str]:
    """跑一段子进程脚本，返回 (退出码, 耗时秒, 输出)。"""
    import os

    script = "import os, sys, time\n" + textwrap.dedent(body).replace(
        "REPO", str(Path(__file__).resolve().parents[1])
    )
    started = time.monotonic()
    completed = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        timeout=120,
        env={**os.environ, "QT_QPA_PLATFORM": "offscreen"},
    )
    return completed.returncode, time.monotonic() - started, completed.stdout + completed.stderr


def test_wait_or_exit_actually_waits_for_running_workers():
    """退出闸门要**真的等**（用户那次闪退的现场就是"退出了但线程还在跑"）。

    用耗时判定：线程睡 1s，闸门等 3s —— 若闸门没等，耗时会明显短于 1s。
    """
    code, elapsed, output = _run_script(
        """
        sys.path.insert(0, "REPO")
        from PySide6.QtCore import QThread
        from tu_shell_agent.ui import workers

        class Sleeper(QThread):
            def run(self):
                time.sleep(1.0)

        workers.track(Sleeper())
        workers.wait_or_exit(3000)
        print("running-after:", [w.isRunning() for w in workers.running()])
        """
    )
    assert code == 0, output
    assert "running-after: []" in output, output
    assert elapsed >= 0.8, f"闸门没有等线程结束（只用了 {elapsed:.2f}s）"


def test_wait_or_exit_does_not_hang_on_a_stuck_worker():
    """等不到就用 os._exit 立刻走：卡住的线程不该把退出拖死。

    线程睡 10s、闸门只等 200ms —— 进程必须在远小于 10s 内退出，且退出码为 0（不是 abort）。
    """
    code, elapsed, output = _run_script(
        """
        sys.path.insert(0, "REPO")
        from PySide6.QtCore import QThread
        from tu_shell_agent.ui import workers

        class Sleeper(QThread):
            def run(self):
                time.sleep(10.0)

        workers.track(Sleeper())
        workers.wait_or_exit(200)
        print("不应该走到这里")
        """
    )
    assert code == 0, f"退出码 {code}（abort 时是负数）：{output}"
    assert "不应该走到这里" not in output, "闸门没有及时退出"
    assert elapsed < 5.0, f"卡住的线程把退出拖了 {elapsed:.1f}s"
