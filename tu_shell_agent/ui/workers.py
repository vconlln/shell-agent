"""后台线程托管：保住引用、退出前等它们结束。

两个坑都真实踩过（本机 coredump 确认 **SIGABRT**，用户看到的就是"选模型直接闪退"）：

1. `self._worker = ModelsWorker(...)` 这样的**单个字段**被下一次请求覆盖时，还在跑的
   `QThread` 就失去了最后一个引用，被 Python GC 掉 —— Qt 随即 abort：
   `QThread: Destroyed while thread '' is still running`；
2. 关窗/退出时线程还在跑，Qt 在析构时同样 abort。

所以这里做两件事：**集中持有引用**（而不是散在各个字段里），以及**退出前统一等待**；
等不到就 `os._exit()`，宁可跳过解释器清理，也不要让进程崩在退出路径上。
"""

from __future__ import annotations

import os
from typing import Any

DEFAULT_WAIT_MS = 5_000

# 进程级的"还在跑的线程"名单：start() 加入，finished 时移除。
# 它同时解决了"引用被覆盖"和"退出时还在跑"这两件事，所以是模块级的，不挂在某个窗口上
# —— 窗口可能先于线程被销毁。
_running: list[Any] = []


def track(worker: Any) -> Any:
    """托管一个 QThread 并启动它。同一个 worker 重复 track 是幂等的。"""
    if worker not in _running:
        _running.append(worker)
        worker.finished.connect(lambda: _forget(worker))
    worker.start()
    return worker


def _forget(worker: Any) -> None:
    if worker in _running:
        _running.remove(worker)


def running() -> list[Any]:
    """**真的还在跑**的那些（按线程自身状态判断，不看名单里有没有）。

    不能只依赖 `finished` 信号：那是排队投递的，调用方不跑事件循环（比如退出路径上）
    就收不到，名单会留下已经结束的线程 —— 判断"还在不在跑"必须问线程自己。
    """
    return [worker for worker in _running if worker.isRunning()]


def wait_all(timeout_ms: int = DEFAULT_WAIT_MS) -> list[Any]:
    """等所有托管线程结束；返回**超时后仍在跑**的那些。"""
    still_running: list[Any] = []
    for worker in list(_running):
        if worker.isRunning() and not worker.wait(timeout_ms):
            still_running.append(worker)
    for worker in list(_running):
        if not worker.isRunning():
            _forget(worker)            # 结束后摘掉，名单不会越攒越多
    return still_running


def wait_or_exit(timeout_ms: int = DEFAULT_WAIT_MS) -> None:
    """退出前的最后一道闸：还有线程没结束就立刻退出进程，避免 Qt 在析构时 abort。

    正常情况（线程都收干净了）什么都不做，让解释器照常清理。
    """
    if wait_all(timeout_ms):
        os._exit(0)
