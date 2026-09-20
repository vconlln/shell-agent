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
from ..types import RunConfig, RunEvent


class EngineWorker(QThread):
    event = Signal(object)                 # RunEvent
    finished_result = Signal(object)       # LoopResult
    confirm_requested = Signal(object)     # {"round": int, "script_path": str, "script": str}
    failed = Signal(str)                   # 引擎抛出的意外异常（已在 run() 里兜住）

    def __init__(self, *, opencode: OpencodePort, toolchain: ToolchainPort,
                 store: RunStorePort | None = None, config: RunConfig,
                 parent: Any = None) -> None:
        """注意：**没有** trusted 参数。要不要跳过确认是模板的属性（`TemplateSpec.trusted`），
        由编排层判定；在线程桥上再放一个同名开关只会让人以为"传 True 就免确认了"，
        而它根本不会被读到。"""
        super().__init__(parent)
        self._opencode = opencode
        self._toolchain = toolchain
        self._store = store
        self._config = config
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
        # 放行之后再查一次取消：cancel() 会把这个关口一并打开（否则线程会一直等下去），
        # 但如果"取消"先到、用户随后又点了确认框的"执行"，只看 _confirm_answer 就会放行 ——
        # 于是被取消的脚本照样执行、还落成 succeeded。取消的语义是"拒绝"，以它为准。
        if self._cancel.is_set():
            return False
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


class ModelsWorker(QThread):
    """取 opencode 可用模型列表（`opencode models`）。

    放线程里：要起一个子进程，在 UI 线程里跑会把窗口冻住；而且这一条是"用户点一下才做"
    的事，没必要预取。外部世界的调用放在适配器层（`opencode_adapter.models`），
    界面这里只负责线程与信号。
    """

    done = Signal(object)   # list[str]：provider/model
    failed = Signal(str)

    def __init__(self, opencode_path: str = "", parent: Any = None) -> None:
        super().__init__(parent)
        self._opencode_path = opencode_path

    def run(self) -> None:  # noqa: D102 - QThread
        try:
            from ..opencode_adapter.models import list_models

            self.done.emit(list_models(self._opencode_path or "opencode"))
        except BaseException as error:  # noqa: BLE001 - 线程里绝不能让异常逃逸
            self.failed.emit(str(error))


class DetectWorker(QThread):
    """环境探测也放线程里：`detect_all` 会起若干子进程（各自跑一次 `--version`），
    在 UI 线程里跑会把窗口冻住好几秒 —— 用户还以为程序挂了。
    """

    done = Signal(object)   # DetectionReport
    failed = Signal(str)    # 探测本身炸了（与"探测完成但发现问题"是两回事）

    def __init__(
        self,
        overrides: dict[str, str] | None = None,
        parent: Any = None,
        *,
        backend_id: str = "opencode",
        command: str = "",
    ) -> None:
        super().__init__(parent)
        self._overrides = dict(overrides or {})
        self._backend_id = str(backend_id or "opencode")
        self._command = str(command or "")

    def run(self) -> None:  # noqa: D102 - QThread
        try:
            # 函数内 import：本模块被 ui 的控制器在启动路径上导入，
            # 顶层拉进 shell_toolchain / 注册表会让"只想建个窗口"也付出这条依赖链的代价。
            from ..agent_backends import detect_environment

            report = detect_environment(
                self._backend_id, self._command, overrides=self._overrides
            )
        except BaseException as error:  # noqa: BLE001 - 线程里绝不能让异常逃逸
            self.failed.emit(str(error))
            return
        self.done.emit(report)


class BackendProbeWorker(QThread):
    """探测"当前后端的命令能不能用"（跑一次它的版本命令）。

    与 DetectWorker 分开：这里只要那一条命令的结论，不需要连带探测 bash/shellcheck，
    而设置页的「检测」按钮按的就是这个语义（用户想知道的是"我填的这条命令对不对"）。
    子进程仍然在适配器层起：本类只负责线程与信号。
    """

    done = Signal(object)   # ProbeResult
    failed = Signal(str)

    def __init__(self, backend_id: str, command: str = "", parent: Any = None) -> None:
        super().__init__(parent)
        self._backend_id = str(backend_id or "")
        self._command = str(command or "")

    def run(self) -> None:  # noqa: D102 - QThread
        try:
            from ..agent_backends import probe_backend

            self.done.emit(probe_backend(self._backend_id, self._command))
        except BaseException as error:  # noqa: BLE001 - 线程里绝不能让异常逃逸
            self.failed.emit(str(error))


class ChatWorker(QThread):
    """一次自由对话（或在既有会话里问一句话）。

    为什么要单独一个 worker：chat 是一次 HTTP 往返（可能几十秒），放在主线程会把界面冻住。
    与 EngineWorker 的区别是它**只说话**：不带结构化输出 schema、不产出 LoopResult、
    也不会让任何脚本被执行 —— 对话里写出来的脚本要先进中栏，再走"改后重跑"
    （shellcheck + 人工确认）才可能被执行。
    """

    delta = Signal(str)      # 流式增量（界面据此显示"正在说话"）
    done = Signal(str)       # 完整回复
    failed = Signal(str)     # 这一轮对话失败（网络/上游拒绝/取消）

    def __init__(self, *, opencode: Any, timeout_ms: int, parent: Any = None) -> None:
        super().__init__(parent)
        self._opencode = opencode
        self._timeout_ms = timeout_ms
        self._cancel = threading.Event()
        self._request: tuple[str, str, str, str] | None = None

    def submit(self, session_id: str, message: str, preamble: str = "", model: str = "") -> None:
        """必须在 start() 之前调用。

        `preamble` 只在会话刚建立时用来交代上下文；`model` 是**这一条消息**要用的模型
        （`provider/model`），留空表示沿用会话/agent 文件里的那个。
        """
        self._request = (session_id, message, preamble, model)

    def cancel(self) -> None:
        self._cancel.set()

    def cancel_token(self) -> threading.Event:
        return self._cancel

    def run(self) -> None:  # noqa: D102 - QThread
        if self._request is None:
            self.failed.emit("ChatWorker.submit() 未被调用")
            return
        session_id, message, preamble, model = self._request
        # 只带非空模型：替身（测试）与旧调用方的 chat() 可能没有这个形参，
        # 无条件传会把它们全部打断。
        extra = {"model": model} if model else {}
        try:
            reply = self._opencode.chat(
                session_id,
                message,
                self._timeout_ms,
                on_delta=self.delta.emit,
                cancel=self._cancel,
                system_preamble=preamble,
                **extra,
            )
        except BaseException as error:  # noqa: BLE001 - 线程里绝不能让异常逃逸
            self.failed.emit(str(error))
            return
        self.done.emit(reply or "")
