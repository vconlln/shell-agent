"""把引擎跑在 QThread 里：事件经信号回主线程，取消与确认用线程原语握手。

线程纪律（不可违反）：本模块的 run() 里**不得触碰任何 Qt 控件**；一切 UI 更新都靠 emit。
"""

from __future__ import annotations

import threading
from dataclasses import replace
from typing import Any, Callable

from PySide6.QtCore import QThread, Signal

from ..orchestrator.loop import LoopInput, LoopPorts, LoopResult, run_loop
from ..ports import OpencodePort, RunStorePort, ToolchainPort
from ..types import DetectionReport, RunConfig, RunEvent


class EngineWorker(QThread):
    event = Signal(object)                 # RunEvent
    finished_result = Signal(object)       # LoopResult
    confirm_requested = Signal(object)     # {"round": int, "script_path": str, "script": str}
    failed = Signal(str)                   # 引擎抛出的意外异常（已在 run() 里兜住）

    def __init__(self, *, opencode: OpencodePort, toolchain: ToolchainPort,
                 store: RunStorePort | None = None, config: RunConfig,
                 trusted: bool = False, parent: Any = None) -> None:
        super().__init__(parent)
        self._opencode = opencode
        self._toolchain = toolchain
        self._store = store
        self._config = config
        self.trusted = trusted
        self._cancel = threading.Event()
        self._confirm_answer: bool | None = None
        self._confirm_gate = threading.Event()
        self._input: LoopInput | None = None
        self._entry: Callable[[LoopPorts, Any], LoopResult] | None = None
        self._ports: LoopPorts | None = None

    # ── 主线程调用 ────────────────────────────────────────────────
    def submit(self, input_: LoopInput) -> None:
        """第 1 轮入口：设置本次运行输入（必须在 start() 之前调用）。"""
        self._input = input_
        self._entry = None
        self._ports = None

    def submit_entry(
        self,
        entry: Callable[[LoopPorts, Any], LoopResult],
        ports: LoopPorts,
    ) -> None:
        """通用入口：续跑（`resume_repair`）与改后重跑（`verify_and_execute`）走这里。

        编排层的三个入口输入类型不同、返回类型相同，所以线程桥只需要认"给我 ports 和取消
        令牌、还我一个 LoopResult"这一个形状 —— 否则每加一个入口就要改一次线程桥。
        ports 里的 confirm/emit 由 worker 线程补上（确认要回主线程、事件要走信号）。
        """
        self._entry = entry
        self._ports = ports
        self._input = None

    def cancel(self) -> None:
        self._cancel.set()
        self._confirm_gate.set()  # 若正阻塞在确认上，一并放行（按"拒绝"处理）

    def answer_confirm(self, approved: bool) -> None:
        self._confirm_answer = approved
        self._confirm_gate.set()

    def cancel_token(self) -> threading.Event:
        return self._cancel

    # ── worker 线程 ───────────────────────────────────────────────
    def _confirm(self, round_no: int, script_path: str, script: str, trusted: bool) -> bool:
        if trusted:
            return True
        self._confirm_answer = None
        self._confirm_gate.clear()
        self.confirm_requested.emit({"round": round_no, "script_path": script_path, "script": script})
        self._confirm_gate.wait()
        return bool(self._confirm_answer)

    def _emit(self, event: RunEvent) -> None:
        self.event.emit(event)

    def run(self) -> None:  # noqa: D102 - QThread
        base = self._ports if self._ports is not None else getattr(self._input, "ports", None)
        if base is None:
            self.failed.emit("EngineWorker.submit()/submit_entry() 未被调用")
            return
        ports = LoopPorts(
            opencode=self._opencode,
            toolchain=self._toolchain,
            confirm=self,
            store=self._store if self._store is not None else base.store,
            emit=self._emit,
        )
        try:
            if self._entry is not None:
                result = self._entry(ports, self._cancel)
            else:
                assert self._input is not None
                # 用 replace 而不是重新拼一个 LoopInput：入参以后再加字段时，
                # 这里不必跟着改（漏改会静默丢掉新字段）。
                result = run_loop(replace(self._input, ports=ports, cancel=self._cancel))
        except BaseException as error:  # noqa: BLE001 - 线程里绝不能让异常逃逸
            self.failed.emit(str(error))
            return
        self.finished_result.emit(result)

    # ConfirmPort 协议
    def confirm(self, round_no: int, script_path: str, script: str, trusted: bool) -> bool:
        return self._confirm(round_no, script_path, script, trusted)


class DetectWorker(QThread):
    """环境探测也放线程里：`detect_all` 会起若干子进程（各自跑一次 `--version`），
    在 UI 线程里跑会把窗口冻住好几秒 —— 用户还以为程序挂了。
    """

    done = Signal(object)   # DetectionReport
    failed = Signal(str)    # 探测本身炸了（与"探测完成但发现问题"是两回事）

    def __init__(self, overrides: dict[str, str] | None = None, parent: Any = None) -> None:
        super().__init__(parent)
        self._overrides = dict(overrides or {})

    def run(self) -> None:  # noqa: D102 - QThread
        try:
            # 函数内 import：本模块被 ui 的控制器在启动路径上导入，
            # 顶层拉进 shell_toolchain 会让"只想建个窗口"也付出这条依赖链的代价。
            from ..shell_toolchain.detect import detect_all, system_deps

            report = detect_all(system_deps(self._overrides))
        except BaseException as error:  # noqa: BLE001 - 线程里绝不能让异常逃逸
            self.failed.emit(str(error))
            return
        self.done.emit(report)
