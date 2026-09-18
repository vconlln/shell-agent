"""适配器层的离线测试：用进程内 stub serve 驱动**真实的** `OpencodeAdapter`。

不碰真实 opencode、不出网：stub 是本进程里的 `ThreadingHTTPServer`，只实现本适配器用到
的端点并把收到的请求记录在案；唯一被替换的是「起子进程」这一步
（`opencode_adapter.start_serve` → 指向 stub 的 `ServeHandle`，`pid=None` 故不会杀任何进程）。
其余全部走真实代码路径：`start()` 的建会话与 Basic 认证、`generate()` 的请求体与响应映射、
SSE 订阅线程的事件分发与权限自动拒绝。

之所以固化这些用例：15 条已提交单测只覆盖 `events.py`/`server.py` 的纯函数，而这几件最容易写错的
事（结构化输出双字段名、`info.error` 形状、权限拒绝端点、`format` 请求字段）此前只在一次性脚本里验过。
"""

from __future__ import annotations

import base64
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable

import httpx
import pytest

import tu_shell_agent.opencode_adapter as adapter_mod
from tu_shell_agent.opencode_adapter import OpencodeAdapter
from tu_shell_agent.opencode_adapter.agent_file import AGENT_NAME
from tu_shell_agent.opencode_adapter.server import ServeHandle
from tu_shell_agent.types import GeneratedScript

PASSWORD = "stub-server-password"
EXPECTED_AUTH = "Basic " + base64.b64encode(f"opencode:{PASSWORD}".encode()).decode()
SESSION_ID = "ses_stub_1"
WAIT_S = 5.0

SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["script", "notes", "assumptions"],
    "properties": {"script": {"type": "string"}, "notes": {"type": "string"}},
}


def _structured(script: str = "#!/usr/bin/env bash\necho hi\n") -> dict[str, Any]:
    return {"script": script, "notes": "桩数据", "assumptions": ["假设 A"]}


def wait_until(predicate: Callable[[], bool], timeout_s: float = WAIT_S) -> bool:
    """有界等待（只在断言可观测副作用时用，不靠 sleep 猜时序）。"""
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return predicate()


class _QuietThreadingHTTPServer(ThreadingHTTPServer):
    """收尾时客户端关闭 SSE 连接会产生 ConnectionReset，不该打印成服务端异常。"""

    def handle_error(self, request, client_address) -> None:  # type: ignore[no-untyped-def]
        import sys

        if isinstance(sys.exc_info()[1], (ConnectionResetError, BrokenPipeError)):
            return
        super().handle_error(request, client_address)


class StubServe:
    """opencode serve 的进程内替身：记录收到的请求，并可往 SSE 流里推帧。"""

    def __init__(self) -> None:
        self.session_id = SESSION_ID
        self.session_bodies: list[dict] = []
        self.message_bodies: list[dict] = []
        self.rejects: list[tuple[str, dict]] = []
        self.aborts: list[str] = []
        self.requests: list[tuple[str, str, str]] = []
        self.unauthorized_paths: list[str] = []
        self.message_response: dict = {"info": {"structured": _structured()}}
        # 自由对话的响应：没有 structured，正文在 parts 里
        self.chat_response: dict = {
            "info": {"id": "msg_1", "role": "assistant"},
            "parts": [{"type": "text", "text": "这是模型的回答。"}],
        }
        # 建会话的响应可替换：用来测 start() 失败时是否自我收尸（默认保持原行为）。
        self.session_response: tuple[int, Any] = (200, {"id": SESSION_ID, "title": None})
        self.on_message: Callable[[], None] | None = None
        # abort 到达时的钩子：真实 serve 收到 abort 会结束这次生成并返回响应，
        # 用它让被挂起的 POST 也能返回，从而测出"abort 真的缩短了等待"。
        self.on_abort: Callable[[], None] | None = None
        self._frames: list[str] = []
        self._cond = threading.Condition()
        self._stopping = False

        stub = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *args) -> None:  # 静音
                return

            # ── 工具 ────────────────────────────────────────────────────
            def _authorized(self) -> bool:
                header = self.headers.get("authorization", "")
                stub.requests.append((self.command, self.path, header))
                if header == EXPECTED_AUTH:
                    return True
                stub.unauthorized_paths.append(self.path)
                return False

            def _send_json(self, code: int, payload: Any) -> None:
                body = json.dumps(payload, ensure_ascii=False).encode()
                self.send_response(code)
                self.send_header("content-type", "application/json")
                self.send_header("content-length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def _read_body(self) -> dict:
                length = int(self.headers.get("content-length") or 0)
                raw = self.rfile.read(length) if length else b""
                return json.loads(raw or b"{}")

            def _chunk(self, data: bytes) -> None:
                self.wfile.write(b"%x\r\n" % len(data) + data + b"\r\n")
                self.wfile.flush()

            def _stream_events(self) -> None:
                self.send_response(200)
                self.send_header("content-type", "text/event-stream")
                self.send_header("transfer-encoding", "chunked")
                self.end_headers()
                try:
                    self._chunk(b": keep-alive\n\n")  # 注释行：适配器必须忽略
                    while True:
                        frames = stub._take_frames()
                        if frames is None:
                            return
                        for frame in frames:
                            self._chunk(frame.encode("utf-8"))
                except (BrokenPipeError, ConnectionResetError):
                    return

            # ── 路由 ────────────────────────────────────────────────────
            def do_GET(self) -> None:  # noqa: N802
                if not self._authorized():
                    self._send_json(401, {"error": "unauthorized"})
                    return
                if self.path == "/global/health":
                    self._send_json(200, {"healthy": True, "version": "1.18.31"})
                    return
                if self.path == "/event":
                    self._stream_events()
                    return
                self._send_json(404, {"error": "not found"})

            def do_POST(self) -> None:  # noqa: N802
                body = self._read_body()
                if not self._authorized():
                    self._send_json(401, {"error": "unauthorized"})
                    return
                if self.path == "/session":
                    stub.session_bodies.append(body)
                    code, payload = stub.session_response
                    self._send_json(code, payload)
                    return
                if "/permissions/" in self.path:
                    stub.rejects.append((self.path, body))
                    self._send_json(200, True)
                    return
                if self.path.endswith("/abort"):
                    stub.aborts.append(self.path)
                    hook = stub.on_abort
                    if hook is not None:
                        hook()
                    self._send_json(200, True)
                    return
                if self.path.endswith("/message"):
                    stub.message_bodies.append(body)
                    hook = stub.on_message
                    if hook is not None:
                        hook()
                    # 带 format 的是结构化输出（generate），不带的是自由对话（chat）：
                    # 真实 serve 两种情况的 payload 形状不同，桩必须一样地区分，
                    # 否则测试会以为 chat 也能拿到 structured。
                    if "format" in body:
                        self._send_json(200, stub.message_response)
                    else:
                        self._send_json(200, stub.chat_response)
                    return
                self._send_json(404, {"error": "not found"})

        self._server = _QuietThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self._server.server_port}"

    @property
    def port(self) -> int:
        return int(self._server.server_port)

    @property
    def handle(self) -> ServeHandle:
        """指向 stub 的 `ServeHandle`：pid=None，所以 stop() 不会杀任何进程。"""
        return ServeHandle(
            base_url=self.base_url,
            password=PASSWORD,
            port=self.port,
            pid=None,
            log_path="",
            process=None,  # type: ignore[arg-type]
            log_file=None,
        )

    # ── 事件流 ──────────────────────────────────────────────────────────
    def push_frame(self, frame: str) -> None:
        """推一段原始 SSE 文本（可含多行 data:，用于验证跨行拼接）。"""
        with self._cond:
            self._frames.append(frame)
            self._cond.notify_all()

    def push_event(self, event: dict) -> None:
        self.push_frame(f"data: {json.dumps(event, ensure_ascii=False)}\n\n")

    def _take_frames(self) -> list[str] | None:
        with self._cond:
            while not self._frames and not self._stopping:
                self._cond.wait(timeout=0.1)
            if self._stopping:
                return None
            frames, self._frames = self._frames, []
            return frames

    def close(self) -> None:
        with self._cond:
            self._stopping = True
            self._cond.notify_all()
        self._server.shutdown()
        self._server.server_close()


def _delta_event(delta: str, field: str = "text") -> dict:
    return {
        "type": "message.part.delta",
        "properties": {
            "sessionID": SESSION_ID, "messageID": "msg_1", "partID": "prt_1",
            "field": field, "delta": delta,
        },
    }


@pytest.fixture
def stub():
    server = StubServe()
    try:
        yield server
    finally:
        server.close()


@pytest.fixture
def notes() -> list[str]:
    return []


@pytest.fixture
def adapter(notes, stub, monkeypatch):
    """真实适配器 + 只替换「起子进程」这一步。"""
    monkeypatch.setattr(adapter_mod, "start_serve", lambda *args, **kwargs: stub.handle)
    instance = OpencodeAdapter("stub-opencode-not-spawned", note=notes.append)
    yield instance
    instance.dispose()


def test_start_creates_session_with_basic_auth(adapter, stub, tmp_path: Path):
    session_id = adapter.start(str(tmp_path / "run"))

    assert session_id == SESSION_ID
    assert len(stub.session_bodies) == 1
    assert stub.session_bodies[0].get("title")  # 建会话时带 title
    # 认证头由适配器按 ServeHandle.password 现算，stub 只认 opencode:<password>
    assert ("POST", "/session", EXPECTED_AUTH) in stub.requests
    assert stub.unauthorized_paths == []


def test_generate_sends_agent_parts_and_format_schema(adapter, stub, tmp_path: Path):
    session_id = adapter.start(str(tmp_path / "run"))

    adapter.generate(session_id, "把方案实现进模板", SCHEMA, timeout_ms=5_000)

    body = stub.message_bodies[0]
    assert body["agent"] == AGENT_NAME
    assert body["parts"] == [{"type": "text", "text": "把方案实现进模板"}]
    # 结构化输出字段名是 format（不是 outputFormat），且带 schema 与重试次数
    assert body["format"] == {"type": "json_schema", "schema": SCHEMA, "retryCount": 2}
    assert "outputFormat" not in body


def test_structured_output_maps_and_deltas_stream_without_duplication(
    adapter, stub, tmp_path: Path
):
    """一次真实 generate 的两面：响应映射（含 structured_output 回退）与增量渲染去重。"""
    session_id = adapter.start(str(tmp_path / "run"))
    streamed: list[str] = []

    def prelude() -> None:
        stub.push_event(_delta_event("你好"))
        # 一帧拆成两个 data: 行（真实踩过的坑）：解析器必须拼回
        stub.push_frame(
            'data: {"type": "message.part.delta", "properties": {"sessionID": "'
            + SESSION_ID
            + '", "messageID": "msg_1", "partID": "prt_1", "field": "text",\n'
            'data: "delta": "世界"}}\n\n'
        )
        stub.push_event(_delta_event("不该被渲染", field="reasoning"))
        # 整 part 的累计文本（回退通道）：已见过 delta，必须**不再**渲染，否则重复
        stub.push_event(
            {"type": "message.part.updated", "properties": {"part": {"type": "text", "text": "你好世界"}}}
        )
        # 围栏帧：它到达即证明前面所有帧都已按序处理完，无需 sleep 猜时序
        stub.push_event(_delta_event("|END"))
        wait_until(lambda: "|END" in streamed)

    stub.on_message = prelude
    script = adapter.generate(session_id, "msg", SCHEMA, timeout_ms=5_000, on_delta=streamed.append)

    assert script == GeneratedScript(
        script="#!/usr/bin/env bash\necho hi\n", notes="桩数据", assumptions=("假设 A",)
    )
    assert streamed == ["你好", "世界", "|END"]  # 只有 delta（含跨行拼接），整 part 未重复渲染

    # 回退字段：JS SDK 文档写作 structured_output，两个都要认
    stub.on_message = None
    stub.message_response = {"info": {"structured_output": _structured("# fallback\n")}}
    fallback = adapter.generate(session_id, "msg", SCHEMA, timeout_ms=5_000)

    assert fallback == GeneratedScript(
        script="# fallback\n", notes="桩数据", assumptions=("假设 A",)
    )


def test_structured_output_failure_is_reported_not_swallowed(adapter, stub, tmp_path: Path):
    session_id = adapter.start(str(tmp_path / "run"))

    # 真实 1.18.31 的形状：message 嵌在 data 里（1.18.31 导出的 OpenAPI：两者都是 required）
    stub.message_response = {
        "info": {
            "error": {
                "name": "StructuredOutputError",
                "data": {"message": "schema 校验连续失败", "retries": 2},
            }
        }
    }
    with pytest.raises(RuntimeError) as failure:
        adapter.generate(session_id, "msg", SCHEMA, timeout_ms=5_000)
    text = str(failure.value)
    assert "StructuredOutputError" in text
    assert "schema 校验连续失败" in text

    # 连结构化字段都没有时也必须报错，绝不能交回空脚本
    stub.message_response = {"info": {}}
    with pytest.raises(RuntimeError) as missing:
        adapter.generate(session_id, "msg", SCHEMA, timeout_ms=5_000)
    assert "结构化输出" in str(missing.value)


def test_permission_ask_is_auto_rejected(adapter, stub, notes, tmp_path: Path):
    session_id = adapter.start(str(tmp_path / "run"))

    stub.push_event(
        {
            "type": "permission.asked",
            "properties": {
                "id": "per_1", "sessionID": session_id, "permission": "bash",
                "patterns": [], "metadata": {}, "always": [],
            },
        }
    )
    # 旧文档名也一并认下（版本差异防护）
    stub.push_event({"type": "permission.updated", "properties": {"id": "per_2", "sessionID": session_id}})

    assert wait_until(lambda: len(stub.rejects) == 2 and len(notes) == 2)
    assert stub.rejects == [
        (f"/session/{session_id}/permissions/per_1", {"response": "reject"}),
        (f"/session/{session_id}/permissions/per_2", {"response": "reject"}),
    ]
    assert any("per_1" in note for note in notes) and any("per_2" in note for note in notes)


class _Cancel:
    """最小取消令牌（与 CLI/UI 传进来的 threading.Event 同形：只有 is_set）。"""

    def __init__(self) -> None:
        self._set = False

    def set(self) -> None:
        self._set = True

    def is_set(self) -> bool:
        return self._set


def test_generate_aborts_when_cancelled_before_sending(adapter, stub, tmp_path: Path):
    """规格 §7.6：生成前已取消 → 不发请求，但仍要 abort 会话。"""
    session_id = adapter.start(str(tmp_path / "run"))
    token = _Cancel()
    token.set()

    with pytest.raises(RuntimeError, match="已取消"):
        adapter.generate(session_id, "msg", SCHEMA, timeout_ms=5_000, cancel=token)

    assert stub.aborts == [f"/session/{session_id}/abort"]
    assert stub.message_bodies == []  # 请求根本没发出去


def test_generate_watchdog_aborts_session_mid_flight(adapter, stub, tmp_path: Path):
    """取消发生在请求在飞期间：看门线程必须发 abort，让在飞的 POST 尽早返回。

    这是"生成阶段（最长 300s）不可取消"这个缺口的回归测试。关键在**时序**：
    stub 收到 POST 后故意迟迟不响应（模拟一次长生成），只有看门线程能在飞期间发 abort；
    而"请求返回后才发现令牌置位"那条兜底路径要等 POST 自己返回，救不了这个场景。
    所以断言 elapsed 远小于 stub 的保持时间，否则该用例在去掉看门线程后仍会通过。
    """
    session_id = adapter.start(str(tmp_path / "run"))
    token = _Cancel()
    entered = threading.Event()  # stub 已收到 POST
    aborted = threading.Event()  # stub 收到了 abort
    HOLD_S = 3.0

    def slow_message() -> None:
        entered.set()
        token.set()  # 用户在生成期间按了取消
        # 模拟 serve 侧的一次长生成：只有 abort 到达才提前结束（否则挂满 HOLD_S）。
        aborted.wait(timeout=HOLD_S)

    stub.on_message = slow_message
    stub.on_abort = aborted.set  # 真实 serve 语义：abort → 结束生成、POST 返回
    stub.message_response = {"info": {}}  # 被打断的响应：没有结构化输出

    started = time.monotonic()
    try:
        with pytest.raises(RuntimeError):
            adapter.generate(session_id, "msg", SCHEMA, timeout_ms=10_000, cancel=token)
        elapsed = time.monotonic() - started
    finally:
        aborted.set()  # 无论成败都放行 handler 线程

    assert entered.is_set(), "stub 必须真的收到过 POST"
    # 只有看门线程能在飞期间发 abort；"请求返回后才发现令牌置位"的兜底救不了这个场景。
    assert elapsed < HOLD_S / 2, f"POST 没有被 abort 打断，耗时 {elapsed:.2f}s"
    # abort 与 POST 是两条独立连接，到账有先后：等它落地再断言。
    # 只允许一条：看门线程发过之后，返回后的兜底不该重复发。
    assert wait_until(lambda: stub.aborts == [f"/session/{session_id}/abort"]), stub.aborts
    time.sleep(0.3)  # 给"重复发 abort"留出暴露窗口
    assert stub.aborts == [f"/session/{session_id}/abort"], "不能重复发 abort"


def test_start_failure_stops_serve_and_does_not_leak(monkeypatch, stub, notes, tmp_path: Path):
    """建会话失败时 start() 必须自己收尸：否则 serve 子进程与 SSE 线程泄漏。

    CLI 的 finally 会调 dispose()，恰好掩盖了这一点；这里直接作为库使用来验。
    """
    stopped: list[int] = []

    def fake_start_serve(*_args, **_kwargs):
        handle = stub.handle
        original_stop = handle.stop

        def stop_and_record() -> None:
            stopped.append(1)
            original_stop()

        handle.stop = stop_and_record  # type: ignore[method-assign]
        return handle

    monkeypatch.setattr(adapter_mod, "start_serve", fake_start_serve)
    stub.session_response = (500, {"error": "boom"})

    instance = OpencodeAdapter("stub-opencode-not-spawned", note=notes.append)
    with pytest.raises(httpx.HTTPStatusError):
        instance.start(str(tmp_path / "run"))

    assert stopped == [1]  # serve 句柄被停掉
    assert instance._serve is None and instance._client is None  # 状态被复位
    assert instance._events_thread is None


def test_resume_brings_up_serve_without_creating_a_session(adapter, stub, tmp_path: Path):
    """`resume()` 是"继续修复"能用的前提：起 serve、重写 agent 文件，但**不新建会话**。

    背景：`resume_repair` 复用既有 sessionId，不经过 `run_loop` 的 `start()`。适配器如果没
    起来就直接 `generate`，第一句就是 `RuntimeError("适配器未启动")`，被编排层当成一次契约
    失败吞掉 —— 白烧剩余轮次后收在 needs_human（"继续修复"按钮实际上是坏的）。
    """
    run_dir = tmp_path / "run"

    adapter.resume(str(run_dir))

    # 不新建会话：修复的语义是"接着那个会话继续"
    assert stub.session_bodies == []
    # agent 文件必须重写：它是"opencode 只写不跑"的权限收敛点，续跑同样不能少
    agent_file = run_dir / ".opencode" / "agents" / f"{AGENT_NAME}.md"
    assert agent_file.is_file()
    assert "bash: deny" in agent_file.read_text(encoding="utf-8")

    # 起来之后确实能对着既有会话发起生成
    generated = adapter.generate(SESSION_ID, "接着修", SCHEMA, timeout_ms=5_000)
    assert generated.script
    assert stub.message_bodies[0]["parts"][0]["text"] == "接着修"


# ── 自由对话（chat）与结构化输出（generate）是两条不同的路 ─────────────────


def test_chat_sends_no_format_and_returns_plain_text(adapter, stub, tmp_path: Path):
    """chat 不带 `format`，正文从 parts 里取。

    这条区别是安全属性的一部分：带 format 的那条路产物必须填 script 字段、要过锚点契约、
    最终由引擎执行；chat 只是说话，它写出来的脚本不会被自动执行。
    """
    session_id = adapter.start(str(tmp_path / "run"))

    reply = adapter.chat(session_id, "刚才那条为什么失败？", timeout_ms=5_000)

    assert reply == "这是模型的回答。"
    body = stub.message_bodies[0]
    assert "format" not in body, "chat 不能带结构化输出 schema"
    assert body["parts"] == [{"type": "text", "text": "刚才那条为什么失败？"}]
    assert body["agent"] == AGENT_NAME


def test_chat_passes_preamble_and_streams_deltas(adapter, stub, tmp_path: Path):
    """上下文前言拼在正文前；流式增量要回调出去（界面靠它做"正在说话"）。"""
    session_id = adapter.start(str(tmp_path / "run"))
    stub.chat_response = {"info": {}, "parts": []}          # 只有增量、没有 parts 的版本
    deltas: list[str] = []

    def push() -> None:
        # 必须走 push_event（SSE 帧要有 data: 前缀与空行），直接塞裸 JSON 解析器不认
        stub.push_event(
            {
                "type": "message.part.delta",
                "properties": {
                    "sessionID": SESSION_ID,
                    "messageID": "msg_1",
                    "partID": "prt_1",
                    "field": "text",
                    "delta": "增量回答",
                },
            }
        )

    stub.on_message = push
    reply = adapter.chat(
        session_id, "问题", timeout_ms=5_000, on_delta=deltas.append, system_preamble="背景：方案是清理日志"
    )

    assert "背景：方案是清理日志" in stub.message_bodies[0]["parts"][0]["text"]
    assert reply == "增量回答"      # parts 为空时用增量拼出来的兜底
    assert "".join(deltas) == "增量回答"


def test_generate_still_requires_structured_output(adapter, stub, tmp_path: Path):
    """反向对照：generate 仍然只认 structured，不能被 chat 的响应形状蒙过去。"""
    session_id = adapter.start(str(tmp_path / "run"))
    stub.message_response = {"info": {}, "parts": [{"type": "text", "text": "闲聊"}]}

    with pytest.raises(RuntimeError, match="未返回结构化输出"):
        adapter.generate(session_id, "写脚本", SCHEMA, timeout_ms=5_000)
