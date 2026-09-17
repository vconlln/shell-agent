"""OpencodePort 的 httpx 实现。会话与结构化输出走 HTTP，事件走 SSE。"""

from __future__ import annotations

import threading
from typing import Any, Callable

import httpx

from ..types import GeneratedScript
from .agent_file import AGENT_NAME, write_agent_file
from .events import SseParser, delta_text, event_text, is_permission_ask
from .server import LOOPBACK_OPTIONS, ServeHandle, basic_auth_header, start_serve


class OpencodeAdapter:
    def __init__(self, opencode_path: str, note: Callable[[str], None] | None = None) -> None:
        self._opencode_path = opencode_path
        self._note = note or (lambda _message: None)
        self._serve: ServeHandle | None = None
        self._client: httpx.Client | None = None
        self._auth = ""
        self._stop_events = threading.Event()
        self._events_thread: threading.Thread | None = None
        self._on_delta: Callable[[str], None] | None = None
        self._saw_delta = False

    # ── 生命周期 ────────────────────────────────────────────────────────
    def start(self, run_dir: str, agent_name: str = AGENT_NAME, model: str | None = None) -> str:
        write_agent_file(run_dir, model)
        self._serve = start_serve(self._opencode_path, run_dir)
        try:
            self._auth = basic_auth_header(self._serve.password)
            self._client = httpx.Client(
                base_url=self._serve.base_url,
                headers={"authorization": self._auth},
                timeout=httpx.Timeout(300.0, connect=10.0),
                **LOOPBACK_OPTIONS,
            )
            self._stop_events.clear()
            self._events_thread = threading.Thread(target=self._consume_events, daemon=True)
            self._events_thread.start()

            response = self._client.post("/session", json={"title": f"tu-shell-agent {run_dir}"})
            response.raise_for_status()
            return str(response.json()["id"])
        except BaseException:
            # 到这里 serve 已经起来了、SSE 线程也已经在了；异常上抛前必须自己收尸，
            # 否则 serve 子进程与订阅线程都会泄漏。CLI 的 finally 会调 dispose()，
            # 恰好掩盖了这一点 —— 作为库使用或 Plan 2 复用时就会漏。
            self.dispose()
            self._serve = None
            self._client = None
            self._events_thread = None
            self._auth = ""
            raise

    def dispose(self) -> None:
        self._stop_events.set()
        if self._client is not None:
            self._client.close()
            self._client = None
        if self._serve is not None:
            self._serve.stop()
            self._serve = None

    # ── 事件与权限 ──────────────────────────────────────────────────────
    def _consume_events(self) -> None:
        assert self._serve is not None
        parser = SseParser()
        try:
            with httpx.stream(
                "GET",
                f"{self._serve.base_url}/event",
                headers={"authorization": self._auth, "accept": "text/event-stream"},
                timeout=None,
                **LOOPBACK_OPTIONS,
            ) as response:
                for line in response.iter_lines():
                    if self._stop_events.is_set():
                        return
                    for event in parser.push(line + "\n"):
                        ask = is_permission_ask(event)
                        if ask is not None:
                            self._reject_permission(*ask)
                        # 1.18.31 的增量文本走 message.part.delta；message.part.updated
                        # 带的是整个 part（累计文本）。一旦见过 delta 就不再回退，避免重复。
                        text = delta_text(event)
                        if text is not None:
                            self._saw_delta = True
                        elif not self._saw_delta:
                            text = event_text(event)
                        if text and self._on_delta is not None:
                            self._on_delta(text)
        except Exception as error:  # noqa: BLE001 - 事件流断了不应带崩主流程
            self._note(f"事件流中断：{error}")

    def _reject_permission(self, session_id: str, permission_id: str) -> None:
        """对任何权限询问一律拒绝（规格 §7.2 的安全网）。"""
        if self._client is None:
            return
        self._client.post(
            f"/session/{session_id}/permissions/{permission_id}",
            json={"response": "reject"},
        )
        self._note(f"已自动拒绝 opencode 的权限请求 {permission_id}")

    # ── 生成 ────────────────────────────────────────────────────────────
    def _cancel_requested(self, cancel: Any) -> bool:
        is_set = getattr(cancel, "is_set", None)
        return bool(callable(is_set) and is_set())

    def _abort_quietly(self, session_id: str) -> None:
        """abort 本身失败不该盖住真正的失败原因（取消/超时）。"""
        try:
            self.abort(session_id)
        except Exception as error:  # noqa: BLE001
            self._note(f"session.abort 失败：{error}")

    def generate(
        self,
        session_id: str,
        message: str,
        schema: dict[str, Any],
        timeout_ms: int,
        on_delta: Callable[[str], None] | None = None,
        cancel: Any = None,
    ) -> GeneratedScript:
        if self._client is None:
            raise RuntimeError("适配器未启动")
        if self._cancel_requested(cancel):
            # 规格 §7.6：在飞之前就取消 → 不发出请求，但要 abort 掉会话。
            self._abort_quietly(session_id)
            raise RuntimeError("已取消")

        self._on_delta = on_delta
        self._saw_delta = False
        stop_watcher = threading.Event()
        # 看门线程与"返回后才发现已取消"的兜底都会想发 abort；用集合记下已经发过的，
        # 避免同一次 generate 连发两条 abort（幂等，但白费一次请求）。
        aborted_by_watchdog: set[str] = set()
        # 生成最长可到 generate_timeout_ms（默认 300s），是整个流程里最长的阻塞点。
        # POST 一旦发出就无法从外部打断，所以用一个轻量看门线程在令牌置位时调 abort，
        # 让 serve 侧尽快结束这次生成，POST 随之返回。
        watcher = threading.Thread(
            target=self._watch_cancel,
            args=(session_id, cancel, stop_watcher, aborted_by_watchdog),
            daemon=True,
        )
        if cancel is not None:
            watcher.start()

        try:
            try:
                response = self._client.post(
                    f"/session/{session_id}/message",
                    json={
                        "agent": AGENT_NAME,
                        "parts": [{"type": "text", "text": message}],
                        "format": {"type": "json_schema", "schema": schema, "retryCount": 2},
                    },
                    timeout=timeout_ms / 1000.0,
                )
            except httpx.TimeoutException:
                # 规格 §7.6：超时 → session.abort()，否则 serve 侧还在后台生成。
                self._abort_quietly(session_id)
                raise
            response.raise_for_status()
            if self._cancel_requested(cancel):
                # 请求在取消之后才返回：这一轮的产出不可信，按取消处理。
                # 看门线程通常已经发过 abort 了（那次 abort 正是 POST 能返回的原因），
                # 别重复发。
                if session_id not in aborted_by_watchdog:
                    self._abort_quietly(session_id)
                raise RuntimeError("已取消")
            payload = response.json()
            info = payload.get("info") or {}
            error = info.get("error")
            if isinstance(error, dict) and error.get("name"):
                detail = (error.get("data") or {}).get("message", "")
                raise RuntimeError(f"opencode 返回错误 {error['name']}：{detail}")
            # 真实 1.18.31 的 OpenAPI 把结构化结果放在 AssistantMessage.structured；
            # JS SDK 文档写的是 structured_output —— 两个都认，避免版本漂移。
            structured = info.get("structured")
            if not isinstance(structured, dict):
                structured = info.get("structured_output")
            if not isinstance(structured, dict):
                raise RuntimeError(
                    "opencode 未返回结构化输出（已查 info.structured 与 info.structured_output）"
                )
            script = structured.get("script")
            if not isinstance(script, str) or not script.strip():
                raise RuntimeError("结构化输出缺少 script 字段（或 script 为空）")
            assumptions = structured.get("assumptions") or []
            return GeneratedScript(
                script=script,
                notes=str(structured.get("notes") or ""),
                assumptions=tuple(str(item) for item in assumptions),
            )
        finally:
            self._on_delta = None
            stop_watcher.set()
            if cancel is not None:
                watcher.join(timeout=1.0)

    def _watch_cancel(
        self, session_id: str, cancel: Any, stop: threading.Event, aborted: set[str]
    ) -> None:
        """取消看门线程：令牌置位就 abort 会话，让在飞的 POST 尽快返回。"""
        while not stop.wait(0.2):
            if self._cancel_requested(cancel):
                aborted.add(session_id)
                self._abort_quietly(session_id)
                return

    def abort(self, session_id: str) -> None:
        if self._client is not None:
            self._client.post(f"/session/{session_id}/abort")


__all__ = ["OpencodeAdapter", "AGENT_NAME"]
