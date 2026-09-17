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
        self.on_message: Callable[[], None] | None = None
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
                    self._send_json(200, {"id": stub.session_id, "title": body.get("title")})
                    return
                if "/permissions/" in self.path:
                    stub.rejects.append((self.path, body))
                    self._send_json(200, True)
                    return
                if self.path.endswith("/abort"):
                    stub.aborts.append(self.path)
                    self._send_json(200, True)
                    return
                if self.path.endswith("/message"):
                    stub.message_bodies.append(body)
                    hook = stub.on_message
                    if hook is not None:
                        hook()
                    self._send_json(200, stub.message_response)
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
