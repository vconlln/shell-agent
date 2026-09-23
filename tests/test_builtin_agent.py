"""内置 agent：直连模型 API（`agent_backends/api_client.py` + `builtin_agent.py`）。

用户要求："我要做的是自己的 agent，你也可以自己做 agent 不用调用别的后端"。
这一路的两个卖点必须在用例里钉住：

1. **快**：一轮对话是"一个 HTTP 请求"，不起任何进程（CLI 后端每轮要起 node，冷启动几秒）；
2. **看得见思考过程**：`reasoning_content` / `thinking_delta` 逐字回调给界面，
   对话记录里分成「思考过程」与「回复」两段（用户明确要求）。

HTTP 用 `httpx.MockTransport` 造假，不需要网络；SSE 响应体按厂商真实的形状拼。
"""

from __future__ import annotations

import json
from collections.abc import Callable

import httpx
import pytest

from tu_shell_agent.agent_backends.api_client import (
    ANSWER_HEADER,
    THINKING_HEADER,
    ApiError,
    ModelApiClient,
    chat_completions_url,
    models_url,
)
from tu_shell_agent.agent_backends.builtin_agent import BuiltinAdapter

CONTRACT_REPLY = (
    "===TU-SCRIPT===\n#!/bin/bash\nset -euo pipefail\necho hi\n"
    "===TU-NOTES===\n说明\n===TU-ASSUMPTIONS===\n无\n===TU-END===\n"
)


def _sse(*payloads: dict | str) -> bytes:
    """拼一段 SSE 响应体（含 `data:` 行与 [DONE]）。"""
    body = ""
    for payload in payloads:
        data = payload if isinstance(payload, str) else json.dumps(payload, ensure_ascii=False)
        body += f"data: {data}\n\n"
    return (body + "data: [DONE]\n\n").encode("utf-8")


def _openai_chunk(content: str = "", reasoning: str = "") -> dict:
    delta: dict[str, str] = {}
    if reasoning:
        delta["reasoning_content"] = reasoning
    if content:
        delta["content"] = content
    return {"choices": [{"delta": delta}]}


def _client(handler: Callable[[httpx.Request], httpx.Response], **kwargs) -> ModelApiClient:
    return ModelApiClient(
        base_url="https://api.example.com/v1",
        api_key="sk-test",
        transport=httpx.MockTransport(handler),
        **kwargs,
    )


# ── 地址拼接 ──────────────────────────────────────────────────────────


def test_urls_are_built_leniently():
    """用户填的 base 形式五花八门：结尾斜杠、已含 /v1、已含完整路径都要认。"""
    assert chat_completions_url("https://api.deepseek.com/v1", "openai") == (
        "https://api.deepseek.com/v1/chat/completions"
    )
    assert chat_completions_url("https://api.deepseek.com/v1/", "openai") == (
        "https://api.deepseek.com/v1/chat/completions"
    )
    assert chat_completions_url("https://api.anthropic.com", "anthropic") == (
        "https://api.anthropic.com/v1/messages"
    )
    assert chat_completions_url("https://api.anthropic.com/v1", "anthropic") == (
        "https://api.anthropic.com/v1/messages"
    )
    assert models_url("https://api.deepseek.com/v1", "openai") == (
        "https://api.deepseek.com/v1/models"
    )
    with pytest.raises(ApiError):
        chat_completions_url("", "openai")


# ── 流式对话 ──────────────────────────────────────────────────────────


def test_streaming_chat_splits_reasoning_and_answer():
    """思考与正文分开回调，并且在切段时插入标题 —— 用户要在界面上看到思考过程。"""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            content=_sse(
                _openai_chunk(reasoning="先看方案"),
                _openai_chunk(reasoning="，再决定"),
                _openai_chunk(content="答案"),
                _openai_chunk(content="来了"),
            ),
        )

    client = _client(handler)
    seen: list[tuple[str, str]] = []
    completion = client.stream_chat(
        model="deepseek-reasoner",
        messages=[{"role": "user", "content": "在吗"}],
        on_event=lambda event: seen.append((event.kind, event.text)),
    )

    assert completion.reasoning == "先看方案，再决定"
    assert completion.content == "答案来了"
    assert seen == [
        ("reasoning", "先看方案"),
        ("reasoning", "，再决定"),
        ("content", "答案"),
        ("content", "来了"),
    ]


def test_delta_stream_inserts_headers_once_per_section():
    """`on_delta`（界面用）在思考/正文切换时各插一行标题，正文里不出现标记。"""
    from tu_shell_agent.agent_backends.api_client import StreamEvent, delta_stream

    chunks: list[str] = []
    handle = delta_stream(None, chunks.append)
    assert handle is not None
    for kind, text in (("reasoning", "想想"), ("content", "答案"), ("content", "！")):
        handle(StreamEvent(kind, text))

    joined = "".join(chunks)
    assert joined.startswith(f"\n{THINKING_HEADER}\n想想")
    assert f"\n{ANSWER_HEADER}\n答案！" in joined
    assert joined.count(THINKING_HEADER) == 1 and joined.count(ANSWER_HEADER) == 1


def test_anthropic_style_stream_is_understood():
    """Anthropic 风格的 SSE（content_block_delta / thinking_delta）也要认。"""

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path.endswith("/v1/messages")
        assert request.headers.get("x-api-key") == "sk-test"
        return httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            content=_sse(
                {"type": "content_block_delta", "delta": {"type": "thinking_delta", "thinking": "推理"}},
                {"type": "content_block_delta", "delta": {"type": "text_delta", "text": "正文"}},
            ),
        )

    client = ModelApiClient(
        base_url="https://api.anthropic.com",
        api_key="sk-test",
        style="anthropic",
        transport=httpx.MockTransport(handler),
    )
    completion = client.stream_chat(model="claude-sonnet-4-5", messages=[{"role": "user", "content": "hi"}])

    assert (completion.reasoning, completion.content) == ("推理", "正文")


def test_error_statuses_are_translated_into_actions():
    """401/404 要说"该怎么办"，而不是只甩一个状态码。"""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"error": {"message": "Invalid API key"}})

    client = _client(handler)
    with pytest.raises(ApiError) as error:
        client.stream_chat(model="m", messages=[{"role": "user", "content": "x"}])
    message = str(error.value)
    assert "401" in message and "API key" in message and "设置" in message


def test_connection_error_mentions_the_address():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("boom", request=request)

    client = _client(handler)
    with pytest.raises(ApiError) as error:
        client.stream_chat(model="m", messages=[{"role": "user", "content": "x"}])
    assert "连不上模型 API" in str(error.value) and "api.example.com" in str(error.value)


def test_cancel_stops_the_stream():
    """取消原语（threading.Event）置位后，流要在下一个数据块处停下。"""
    import threading

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            content=_sse(_openai_chunk(content="第一"), _openai_chunk(content="第二")),
        )

    cancel = threading.Event()
    client = _client(handler)
    completion = client.stream_chat(
        model="m",
        messages=[{"role": "user", "content": "x"}],
        cancel=cancel,
        on_event=lambda _event: cancel.set(),
    )
    assert completion.content == "第一"
    assert completion.finish_reason == "cancelled"


# ── 模型列表 ──────────────────────────────────────────────────────────


def test_list_models_reads_the_real_list():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path.endswith("/models")
        assert request.headers["authorization"] == "Bearer sk-test"
        return httpx.Response(
            200,
            json={"data": [{"id": "deepseek-chat"}, {"id": "deepseek-reasoner"}, {"id": "deepseek-chat"}]},
        )

    assert _client(handler).list_models() == ["deepseek-chat", "deepseek-reasoner"]


# ── 适配器：契约、历史、取消 ───────────────────────────────────────────


def _adapter(handler: Callable[[httpx.Request], httpx.Response], **kwargs) -> BuiltinAdapter:
    options = {"model": "deepseek-reasoner", **kwargs}
    return BuiltinAdapter(
        base_url="https://api.example.com/v1",
        api_key="sk-test",
        client_factory=lambda **factory_kwargs: ModelApiClient(
            transport=httpx.MockTransport(handler), **factory_kwargs
        ),
        **options,
    )


def test_generate_parses_the_same_text_contract_as_the_cli_backends():
    """生成走**同一套文本契约**（`===TU-SCRIPT===` …），所以引擎那侧的解析一行都不用改。"""

    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content.decode("utf-8"))
        assert payload["stream"] is True
        assert any("TU-SCRIPT" in str(item.get("content", "")) for item in payload["messages"]), (
            "生成时必须把输出格式要求发给模型"
        )
        return httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            content=_sse(_openai_chunk(content=CONTRACT_REPLY)),
        )

    adapter = _adapter(handler)
    session = adapter.start("/tmp/run", "tu-shell-writer", "deepseek-reasoner")
    script = adapter.generate(session, "写个脚本", {}, 30_000)

    assert script.script.strip().startswith("#!/bin/bash")
    assert script.notes.strip() == "说明"


def test_generate_reports_a_missing_contract_as_a_retryable_error():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            content=_sse(_openai_chunk(content="我不按格式回答")),
        )

    adapter = _adapter(handler)
    session = adapter.start("/tmp/run", "agent", "m")
    with pytest.raises(RuntimeError) as error:
        adapter.generate(session, "写个脚本", {}, 30_000)
    assert "TU-SCRIPT" in str(error.value) or "契约" in str(error.value)


def test_chat_streams_and_keeps_history_for_the_next_turn():
    """对话要流式（用户能看到思考），并且把历史带进下一轮 —— 不然每句都是"第一句"。"""
    seen_payloads: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content.decode("utf-8"))
        seen_payloads.append(payload)
        return httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            content=_sse(_openai_chunk(content="好的")),
        )

    adapter = _adapter(handler)
    session = adapter.start("/tmp/run", "agent", "m")
    chunks: list[str] = []
    reply = adapter.chat(session, "第一问", 30_000, on_delta=chunks.append)

    assert reply == "好的"
    assert chunks[-1] == "好的"
    adapter.chat(session, "第二问", 30_000)

    second = seen_payloads[-1]["messages"]
    assert [item["role"] for item in second] == ["user", "assistant", "user"]
    assert second[0]["content"] == "第一问"
    assert second[1]["content"] == "好的"


def test_chat_without_a_model_says_what_to_do():
    """没填模型时给一句可操作的话（这一路没有"默认模型"可退）。"""
    adapter = BuiltinAdapter(base_url="https://api.example.com/v1", api_key="k")
    session = adapter.start("/tmp/run", "agent", "")
    with pytest.raises(RuntimeError) as error:
        adapter.chat(session, "在吗", 30_000)
    assert "模型" in str(error.value) and "设置" in str(error.value)


def test_resume_reads_the_history_from_the_run_dir(qtbot, tmp_path):
    """`resume(run_dir)` 要把历史从运行目录读回来（与界面回填用的是同一份 chat.jsonl）。"""
    from tu_shell_agent.run_store.sessions import append_chat

    append_chat(str(tmp_path), "user", "上次我问的")
    append_chat(str(tmp_path), "model", "上次它答的")

    adapter = BuiltinAdapter(base_url="https://api.example.com/v1", api_key="k", model="m")
    adapter.resume(str(tmp_path))

    assert [(item["role"], item["content"]) for item in adapter._messages] == [
        ("user", "上次我问的"),
        ("assistant", "上次它答的"),
    ]


def test_abort_stops_an_in_flight_turn():
    """`abort()` 要能让正在读的流停下来（取消按钮必须有用）。"""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            content=_sse(_openai_chunk(content="第一"), _openai_chunk(content="第二")),
        )

    adapter = _adapter(handler)
    session = adapter.start("/tmp/run", "agent", "m")
    reply = adapter.chat(session, "在吗", 30_000, on_delta=lambda _text: adapter.abort(session))
    assert reply == "第一"


def test_builtin_descriptor_is_wired_for_probe_and_model_list():
    """注册表里的内置条目要能探测（真问一次 API）与列模型。"""
    from tu_shell_agent.agent_backends import backend_descriptor, build_adapter
    from tu_shell_agent.agent_backends.backends import builtin as builtin_entry

    descriptor = backend_descriptor("builtin")
    assert descriptor.is_api and not descriptor.is_cli

    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.path)
        return httpx.Response(200, json={"data": [{"id": "deepseek-chat"}]})

    transport = httpx.MockTransport(handler)
    config = {"base_url": "https://api.example.com/v1", "api_key": "sk", "style": "openai"}

    adapter = build_adapter("builtin", "", api=config)
    assert isinstance(adapter, BuiltinAdapter)

    monkey_models = builtin_entry.list_models
    builtin_entry.list_models = lambda cfg: ModelApiClient(  # type: ignore[assignment]
        base_url=cfg["base_url"], api_key=cfg["api_key"], transport=transport
    ).list_models()
    try:
        result = builtin_entry.probe(config)
    finally:
        builtin_entry.list_models = monkey_models  # type: ignore[assignment]

    assert result.ok and "1 个模型可用" in result.detail()
    assert seen and seen[0].endswith("/models")


def test_probe_reports_missing_config_without_network():
    """字段没填时要直接说清楚，而不是发一次注定失败的请求。"""
    from tu_shell_agent.agent_backends.backends.builtin import probe

    result = probe({"base_url": "", "api_key": ""})
    assert not result.ok and "API 地址" in result.message
    result = probe({"base_url": "https://api.example.com/v1", "api_key": ""})
    assert not result.ok and "API key" in result.message


# ── 端到端：用"自己的 agent"跑完整一轮（真实 shellcheck + 真实 bash）─────


def test_our_own_agent_drives_the_whole_loop(tmp_path, shellcheck_path, bash_path):
    """内置 agent 跑完整链路：生成（过契约）→ shellcheck 拦下 → 回灌自修 → 执行成功。

    这条是"自己的 agent 能不能干活"的最终证据：用一个假的模型 API（SSE 里给两轮回复：
    先给一版会被 shellcheck 拦下的脚本，再给修好的），其余全是真实组件
    （真实 `run_loop`、真实 shellcheck、真实 bash、真实运行目录落盘）。
    """
    from tu_shell_agent.orchestrator.loop import LoopInput, LoopPorts, TemplateSpec, run_loop
    from tu_shell_agent.run_store.store import RunStore
    from tu_shell_agent.shell_toolchain.execute import run_script
    from tu_shell_agent.shell_toolchain.shellcheck import run_shellcheck
    from tu_shell_agent.types import DetectionReport, RunConfig

    broken = (
        "#!/usr/bin/env bash\nset -euo pipefail\n# @@TU:BODY@@\n"
        'for f in $(ls); do echo $f; done\necho "done"\n'
    )
    fixed = (
        "#!/usr/bin/env bash\nset -euo pipefail\n# @@TU:BODY@@\n"
        'for f in *; do echo "$f"; done\necho "done"\n'
    )
    replies = [
        f"===TU-SCRIPT===\n{broken}===TU-NOTES===\n首轮\n===TU-ASSUMPTIONS===\n无\n===TU-END===\n",
        f"===TU-SCRIPT===\n{fixed}===TU-NOTES===\n补引号\n===TU-ASSUMPTIONS===\n无\n===TU-END===\n",
    ]
    calls = {"n": 0}
    seen_prompts: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content.decode("utf-8"))
        seen_prompts.append(str(payload["messages"][-1]["content"]))
        index = min(calls["n"], len(replies) - 1)
        calls["n"] += 1
        return httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            content=_sse(_openai_chunk(content=replies[index])),
        )

    run_dir = str(tmp_path / "r1")
    store = RunStore(run_dir)
    store.init()

    class Toolchain:
        def detect(self):
            return DetectionReport(None, None, None, ())

        def shellcheck(self, script_path):
            return run_shellcheck(shellcheck_path, script_path)

        def execute(self, script_path, cwd, timeout_ms, cancel=None, on_stdout=None, on_stderr=None):
            return run_script(bash_path, script_path, cwd, timeout_ms)

    ports = LoopPorts(
        opencode=_adapter(handler, model="deepseek-reasoner"),
        toolchain=Toolchain(),
        confirm=type("C", (), {"confirm": lambda self, *a: True})(),
        store=store,
        emit=lambda event: None,
    )
    result = run_loop(
        LoopInput(
            plan="把一句话拆成单词逐行打印",
            template=TemplateSpec(
                id="single",
                body="#!/usr/bin/env bash\nset -euo pipefail\n# @@TU:BODY@@\n",
                anchors=("@@TU:BODY@@",),
                trusted=True,
            ),
            values={},
            run_dir=run_dir,
            config=RunConfig(
                run_root=str(tmp_path), max_rounds=3,
                generate_timeout_ms=5_000, execute_timeout_ms=10_000,
            ),
            ports=ports,
        )
    )

    assert result.outcome == "succeeded", result
    assert result.rounds == 2, "第一轮应当被 shellcheck 拦下并回灌自修"
    assert calls["n"] == 2, "两轮应当各发一次模型请求（一次 HTTP，不起进程）"
    assert "SC2045" in (tmp_path / "r1" / "attempts" / "1" / "shellcheck.txt").read_text("utf-8")
    stdout = (tmp_path / "r1" / "attempts" / "2" / "stdout.txt").read_text("utf-8")
    assert "script.sh" in stdout and "done" in stdout
    # 第二轮的提示里必须带上 shellcheck 的反馈（回灌自修的证据）
    assert "SC2045" in seen_prompts[-1]
