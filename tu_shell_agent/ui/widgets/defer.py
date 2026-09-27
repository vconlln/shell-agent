"""把"事件处理期间不该做的事"推到事件循环的下一拍。

**为什么需要它**：Qt 在应用样式表 / polish / 遍历控件树的过程中会发 `FontChange`、
`PaletteChange` 这类事件。响应这些事件时**改几何或改文档格式**，等于在 Qt 遍历的中途
动它正在读的结构 —— 单测里表现为整轮用例随机段错误（C 栈落在 libQt6Widgets，
Python 栈落在 `QApplication.setStyleSheet` 的夹具收尾里，实测过）。

延到下一拍没有观感代价（下一帧就生效），却把"改"与"遍历"分开了。
同一个控件连续收到多个事件时只排一次（`_pending` 标记），不会堆积。
"""

from __future__ import annotations

from PySide6.QtCore import QTimer


def schedule_after_event_loop(widget, callback) -> None:
    """在当前事件处理结束后调用一次 `callback`（同一控件重复调用只排一次）。

    **对象已经被销毁就直接跳过**：延后执行意味着"下一拍"时控件可能已经没了
    （用例里把窗口一关、控件随父级一起销毁），这时候去碰它会抛
    `libshiboken: Internal C++ object already deleted`。
    """
    if getattr(widget, "_deferred_call_pending", False):
        return
    widget._deferred_call_pending = True

    def run() -> None:
        try:
            from shiboken6 import isValid

            if not isValid(widget):
                return
        except Exception:  # noqa: BLE001 - 拿不到 shiboken 就照旧执行（不能因为守卫本身失败而不干活）
            pass
        widget._deferred_call_pending = False
        callback()

    QTimer.singleShot(0, run)
