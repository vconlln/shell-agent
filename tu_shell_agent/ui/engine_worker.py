"""把引擎跑在 QThread 里：事件经信号回主线程，取消与确认用线程原语握手。

线程纪律（不可违反）：本模块的 run() 里**不得触碰任何 Qt 控件**；一切 UI 更新都靠 emit。
"""

from __future__ import annotations

import threading
from typing import Any

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

    # ── 主线程调用 ────────────────────────────────────────────────
    def submit(self, input_: LoopInput) -> None:
        """设置本次运行输入（必须在 start() 之前调用）。"""
        self._input = input_

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
        if self._input is None:
            self.failed.emit("EngineWorker.submit() 未被调用")
            return
        ports = LoopPorts(
            opencode=self._opencode,
            toolchain=self._toolchain,
            confirm=self,
            store=self._store if self._store is not None else self._input.ports.store,
            emit=self._emit,
        )
        input_ = LoopInput(
            plan=self._input.plan,
            template=self._input.template,
            values=self._input.values,
            run_dir=self._input.run_dir,
            config=self._config,
            ports=ports,
            agent_name=self._input.agent_name,
            cancel=self._cancel,
        )
        try:
            result = run_loop(input_)
        except BaseException as error:  # noqa: BLE001 - 线程里绝不能让异常逃逸
            self.failed.emit(str(error))
            return
        self.finished_result.emit(result)

    # ConfirmPort 协议
    def confirm(self, round_no: int, script_path: str, script: str, trusted: bool) -> bool:
        return self._confirm(round_no, script_path, script, trusted)
