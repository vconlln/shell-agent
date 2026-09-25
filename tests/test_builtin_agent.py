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
from pathlib import Path

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


# ── 用户的真实场景：DeepSeek 的 Anthropic 兼容端点 ─────────────────────


def test_deepseek_anthropic_base_picks_the_anthropic_style():
    """地址 `https://api.deepseek.com/anthropic` 是 **Anthropic 兼容**端点。

    用户实测就填的它，而风格留在默认的「OpenAI 兼容」—— 请求于是打到
    `/anthropic/chat/completions` 这种不存在的路径上，"检测可用模型"一直转圈。
    地址本身已经说明该选哪一个，所以既要有建议函数、也要在界面上说出来。
    """
    from tu_shell_agent.agent_backends.backends.builtin import (
        STYLE_ANTHROPIC,
        STYLE_OPENAI,
        suggested_style,
    )

    assert suggested_style("https://api.deepseek.com/anthropic") == STYLE_ANTHROPIC
    assert suggested_style("https://api.deepseek.com/anthropic/") == STYLE_ANTHROPIC
    assert suggested_style("https://api.deepseek.com/v1") == STYLE_OPENAI
    assert suggested_style("http://127.0.0.1:11434/v1") == STYLE_OPENAI
    assert suggested_style("") == "", "猜不出来就给空串（不要瞎猜）"


def test_chat_url_for_the_deepseek_anthropic_base_is_correct():
    assert chat_completions_url("https://api.deepseek.com/anthropic", "anthropic") == (
        "https://api.deepseek.com/anthropic/v1/messages"
    )


def test_implied_style_demands_a_real_anthropic_path():
    """只有"路径里独立成段的 anthropic"才算证据 —— 别的写法一律不许自动改判。"""
    from tu_shell_agent.agent_backends.api_client import implied_style

    assert implied_style("https://api.deepseek.com/anthropic") == "anthropic"
    assert implied_style("https://api.deepseek.com/anthropic/") == "anthropic"
    assert implied_style("https://api.deepseek.com/anthropic/v1") == "anthropic"
    assert implied_style("https://gw.example.com/v1/anthropic") == "anthropic"

    # 这几条是"不许瞎猜"的护栏：改判错了会把本来能用的配置弄坏
    assert implied_style("https://api.anthropic.com/v1") == "", (
        "Anthropic 官方 SDK 的写法同样以 /v1 结尾，不能因此改判成 OpenAI 风格"
    )
    assert implied_style("https://anthropic.example.com/v1") == "", "主机名里带 anthropic 不算证据"
    assert implied_style("https://gw.example.com/anthropic-proxy/v1") == "", "只是名字带 anthropic"
    assert implied_style("") == "" and implied_style("https://api.deepseek.com/v1") == ""


def test_mismatched_style_is_reconciled_before_the_request_goes_out():
    """地址能证明风格时**按地址发**：用户实测"地址对、风格错"就是一路 404。

    这条钉的是"线上真实发生的事"：不只是猜一个建议，而是请求真的打到 `/v1/messages`、
    真的带 Anthropic 的鉴权头 —— 否则界面上怎么提示都还是会 404。
    """
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["path"] = request.url.path
        seen["key_header"] = request.headers.get("x-api-key")
        seen["auth_header"] = request.headers.get("authorization")
        seen["body"] = json.loads(request.content.decode("utf-8"))
        return httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            content=_sse({"type": "content_block_delta", "delta": {"type": "text_delta", "text": "好"}}),
        )

    client = ModelApiClient(
        base_url="https://api.deepseek.com/anthropic",     # 用户的地址
        api_key="sk-test",
        style="openai",                                    # 用户留下的风格
        transport=httpx.MockTransport(handler),
    )
    completion = client.stream_chat(model="deepseek-flash", messages=[{"role": "user", "content": "在吗"}])

    assert completion.content == "好"
    assert seen["path"] == "/anthropic/v1/messages", f"请求没有按地址的协议发：{seen['path']}"
    assert seen["key_header"] == "sk-test" and seen["auth_header"] is None
    assert "system" not in seen["body"], "Anthropic 风格的 system 是顶层字段，不能塞进 messages"
    assert client.style == "anthropic" and client.style_requested == "openai"
    assert client.style_note, "改判了就要有话说（静默改判同样让人一头雾水）"
    assert "Anthropic 兼容" in client.style_note and "接口风格" in client.style_note


def test_a_consistent_config_is_left_alone():
    """本来就是对的配置不许被"顺手改判"，也不该多出一句提示。"""
    from tu_shell_agent.agent_backends.api_client import ModelApiClient as Client

    for base, style, path in (
        ("https://api.deepseek.com/v1", "openai", "/v1/chat/completions"),
        ("https://api.anthropic.com/v1", "anthropic", "/v1/messages"),
        ("https://api.anthropic.com", "anthropic", "/v1/messages"),
    ):
        seen: dict = {}

        def handler(request: httpx.Request, _seen=seen) -> httpx.Response:
            _seen["path"] = request.url.path
            return httpx.Response(200, json={})

        client = Client(
            base_url=base, api_key="k", style=style, transport=httpx.MockTransport(handler)
        )
        assert client.style == style, f"{base} 的风格被改判了"
        assert client.style_note == "", f"{base} 多出了一句话：{client.style_note}"
        assert chat_completions_url(client.base_url, client.style).endswith(path)


def test_404_message_names_the_real_request_url_and_the_model():
    """404 要报**真正的请求地址**（不是 base）与模型名 —— 用户就是被这条坑住的。

    只印 base 时，`https://api.deepseek.com/anthropic` 看着完全正确，用户只会说
    "我模型配置好了啊"；印出 `/anthropic/chat/completions` 才能一眼看出路径不对。
    """

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={"error": {"message": "Not Found"}})

    client = ModelApiClient(
        base_url="https://api.deepseek.com/v1",
        api_key="sk-test",
        transport=httpx.MockTransport(handler),
    )
    with pytest.raises(ApiError) as error:
        client.stream_chat(
            model="deepseek-flash", messages=[{"role": "user", "content": "x"}], retries=0
        )
    message = str(error.value)
    assert "https://api.deepseek.com/v1/chat/completions" in message, message
    assert "deepseek-flash" in message, "要报出用的是哪个模型"
    assert "/anthropic" in message, "404 的处理建议里要提一句 /anthropic 该配 Anthropic 风格"


def test_adapter_chat_survives_a_mismatched_style_and_says_what_it_did():
    """适配器这一层：改判后的风格必须**同步进来**（工具协议/消息体都按它拼），并且要出声。

    用户的原始现象就是在这个入口上：对话发出去 → 404。这里连"界面能看到那句说明"一起钉住。
    """
    seen: dict = {}
    notices: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen["path"] = request.url.path
        seen["tools"] = "tools" in json.loads(request.content.decode("utf-8"))
        return httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            content=_sse({"type": "content_block_delta", "delta": {"type": "text_delta", "text": "在"}}),
        )

    adapter = BuiltinAdapter(
        base_url="https://api.deepseek.com/anthropic",
        api_key="sk-test",
        style="openai",
        model="deepseek-flash",
        note=notices.append,
        tools=True,
        client_factory=lambda **kwargs: ModelApiClient(
            transport=httpx.MockTransport(handler), **kwargs
        ),
    )
    session = adapter.start("/tmp/run", "agent", "deepseek-flash")
    reply = adapter.chat(session, "在吗", 30_000)

    assert reply == "在", "Anthropic 风格的流没被当成正文解析 —— 说明风格没同步进适配器"
    assert seen["path"] == "/anthropic/v1/messages"
    assert adapter._style == "anthropic", "适配器还在按 OpenAI 拼请求体/工具描述"
    assert any("Anthropic 兼容" in line for line in notices), f"界面上一句话都没有：{notices}"


def test_model_list_falls_back_to_the_openai_compatible_root():
    """Anthropic 兼容端点**没有** `/v1/models`：要退到同一站点的 OpenAI 兼容 `/models`。

    （DeepSeek 的 `/anthropic/v1/models` 不存在，而 `/models` 是存在的 —— 这样用户即使填的是
    Anthropic 端点，也能取到真实模型列表，而不是一句"取不到"。）
    """
    from tu_shell_agent.agent_backends.api_client import models_url_candidates

    candidates = models_url_candidates("https://api.deepseek.com/anthropic", "anthropic")
    assert candidates[0] == "https://api.deepseek.com/anthropic/v1/models"
    assert "https://api.deepseek.com/models" in candidates

    asked: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        asked.append(str(request.url))
        if request.url.path.endswith("/anthropic/v1/models"):
            return httpx.Response(404, json={"error": {"message": "not found"}})
        return httpx.Response(200, json={"data": [{"id": "deepseek-chat"}, {"id": "deepseek-reasoner"}]})

    client = ModelApiClient(
        base_url="https://api.deepseek.com/anthropic",
        api_key="sk-test",
        style="anthropic",
        transport=httpx.MockTransport(handler),
    )
    models = client.list_models()
    assert models == ["deepseek-chat", "deepseek-reasoner"]
    assert len(asked) == 2, f"没有按候选地址回退：{asked}"


def test_all_model_urls_failing_reports_what_was_tried():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={"error": {"message": "nope"}})

    client = ModelApiClient(
        base_url="https://api.deepseek.com/anthropic",
        api_key="sk-test",
        style="anthropic",
        transport=httpx.MockTransport(handler),
    )
    with pytest.raises(ApiError) as error:
        client.list_models()
    message = str(error.value)
    assert "已尝试" in message and "404" in message


def test_model_list_uses_a_short_timeout():
    """列模型/检测必须**短超时**：不能让界面停在"正在获取"上五分钟（用户实测）。

    老实现用的是对话那档超时（300s），地址不通时界面就一直转圈。这里断言"请求上带的
    超时就是短的那一档"（`httpx` 把超时放进 `request.extensions`），
    再钉住那个常量本身别被调回 300。
    """
    from tu_shell_agent.agent_backends.api_client import LIST_TIMEOUT_S

    assert LIST_TIMEOUT_S <= 30, f"列模型的超时又被调大了：{LIST_TIMEOUT_S}s"

    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["timeout"] = request.extensions.get("timeout")
        return httpx.Response(200, json={"data": []})

    _client(handler).list_models(timeout_s=LIST_TIMEOUT_S)

    assert seen["timeout"] is not None, "请求上没有带超时"
    assert seen["timeout"]["read"] == LIST_TIMEOUT_S


def test_socks_proxy_without_socksio_gives_an_actionable_error(monkeypatch):
    """环境里配了 SOCKS 代理而 httpx 缺 socksio 时，要给一句人话。

    （国内机器上很常见：`ALL_PROXY=socks5://…`。原始报错是
    `Using SOCKS proxy, but the 'socksio' package is not installed`，
    用户看不懂也不知道该怎么办。）
    """
    import httpx as httpx_module

    from tu_shell_agent.agent_backends.api_client import ModelApiClient

    def boom(*_args, **_kwargs):
        raise ImportError("Using SOCKS proxy, but the 'socksio' package is not installed.")

    monkeypatch.setattr(httpx_module, "Client", boom)
    with pytest.raises(ApiError) as error:
        ModelApiClient(base_url="https://api.deepseek.com/v1", api_key="k")
    message = str(error.value)
    assert "SOCKS" in message and ("socksio" in message or "httpx[socks]" in message)
    assert "ALL_PROXY" in message or "环境变量" in message


def test_settings_page_fixes_the_style_when_the_address_proves_it(qtbot):
    """地址能证明协议时，设置页**就地改对**并说明原因（用户就是这么卡住的）。

    只提示是不够的：用户看到的是黄字提示 + 一句 HTTP 404，然后说"我模型配置好了啊"。
    地址是用户自己填的，比下拉框更能说明这个端点说哪种协议 —— 以地址为准，并把改动作说出来。
    """
    from tu_shell_agent.ui.pages.settings_page import SettingsPage

    page = SettingsPage()
    qtbot.addWidget(page)
    page.backend_combo.setCurrentIndex(page.backend_combo.findData("builtin"))
    page.api_style_combo.setCurrentIndex(page.api_style_combo.findData("openai"))

    page.api_base_edit.setText("https://api.deepseek.com/anthropic")

    assert page.api_style_combo.currentData() == "anthropic", "风格没有跟着地址改"
    hint = page.api_style_hint.text()
    assert "Anthropic" in hint and "已按地址改为" in hint, hint
    assert "⚠" not in hint, "已经改对了就不该再报警"


def test_settings_page_still_warns_when_the_address_only_looks_like_one(qtbot):
    """只能"看着像"的时候**不许自动改判**，只给建议（`https://api.anthropic.com/v1` 就是例子）。"""
    from tu_shell_agent.ui.pages.settings_page import SettingsPage

    page = SettingsPage()
    qtbot.addWidget(page)
    page.backend_combo.setCurrentIndex(page.backend_combo.findData("builtin"))
    page.api_base_edit.setText("https://api.anthropic.com")
    page.api_style_combo.setCurrentIndex(page.api_style_combo.findData("anthropic"))
    page._refresh_api_style_hint()
    assert page.api_style_combo.currentData() == "anthropic"
    assert "⚠" not in page.api_style_hint.text(), "这条地址本来就是 Anthropic 风格，不该报警"

    # 换成"以 /v1 结尾 + Anthropic 风格"：有歧义，只提示不代改
    page.api_base_edit.setText("https://api.example.com/v1")
    assert page.api_style_combo.currentData() == "anthropic", "有歧义的地址不许自动改判"
    assert "⚠" in page.api_style_hint.text(), "看着像的地址要给建议"


def test_settings_save_persists_the_corrected_style(qtbot, tmp_path):
    """落盘时的风格必须与地址一致 —— 否则这份坏配置会一直躺在文件里，下次开界面照样 404。"""
    from tu_shell_agent.ui.pages.settings_page import SettingsPage
    from tu_shell_agent.ui.settings import AppSettings

    page = SettingsPage()
    qtbot.addWidget(page)
    # 用 load() 造一份"用户机器上真实存在的那份坏配置"：地址是 Anthropic 端点、风格却是 OpenAI
    settings = AppSettings.load(tmp_path / "settings.json")
    settings.run_root = str(tmp_path / "runs")
    settings.agent_backend = "builtin"
    settings.api_provider = "deepseek-anthropic"
    settings.api_base = "https://api.deepseek.com/anthropic"
    settings.api_style = "openai"
    page.set_settings(settings)
    assert page.api_style_combo.currentData() == "anthropic", "reload() 没有对齐风格"

    path = page.save()
    assert path is not None
    saved = json.loads(Path(path).read_text(encoding="utf-8"))
    assert saved["api_style"] == "anthropic", saved.get("api_style")
    assert saved["api_base"] == "https://api.deepseek.com/anthropic"


# ── 与 opencode 对齐的三件事：超时、重试、上下文预算 ────────────────────


def test_each_call_honors_the_timeout_from_the_engine():
    """生成/对话的超时由引擎给（与命令行后端一致），不能固定用客户端默认值。

    （opencode 那边每次调用也带超时；这一路之前忽略 `timeout_ms`，长请求只能等 300 秒。）
    """
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["timeout"] = request.extensions.get("timeout")
        return httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            content=_sse(_openai_chunk(content="好")),
        )

    adapter = _adapter(handler)
    session = adapter.start("/tmp/run", "agent", "m")
    adapter.chat(session, "在吗", 7_000)

    assert seen["timeout"] is not None, "请求上没有带超时"
    assert seen["timeout"]["read"] == 7.0, f"超时不是引擎给的那个：{seen['timeout']}"


def test_transient_failures_are_retried_but_config_errors_are_not():
    """瞬时故障（503/429/连不上）要退避重试；401 这种配置错误立刻报出来。"""
    attempts: list[int] = []

    def flaky(request: httpx.Request) -> httpx.Response:
        attempts.append(1)
        if len(attempts) == 1:
            return httpx.Response(503, json={"error": {"message": "busy"}})
        return httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            content=_sse(_openai_chunk(content="终于好了")),
        )

    client = _client(flaky)
    completion = client.stream_chat(model="m", messages=[{"role": "user", "content": "x"}])
    assert completion.content == "终于好了" and len(attempts) == 2

    attempts.clear()

    def unauthorized(request: httpx.Request) -> httpx.Response:
        attempts.append(1)
        return httpx.Response(401, json={"error": {"message": "bad key"}})

    with pytest.raises(ApiError):
        _client(unauthorized).stream_chat(model="m", messages=[{"role": "user", "content": "x"}])
    assert len(attempts) == 1, "401 不该重试（重试只是让用户多等十几秒）"


def test_long_history_is_trimmed_to_the_budget():
    """长对话要按预算裁历史（opencode 由 serve 管窗口，这一路得自己管）。"""
    payloads: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        payloads.append(json.loads(request.content.decode("utf-8")))
        return httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            content=_sse(_openai_chunk(content="好")),
        )

    adapter = BuiltinAdapter(
        base_url="https://api.example.com/v1",
        api_key="sk-test",
        model="m",
        max_history_chars=200,
        client_factory=lambda **kwargs: ModelApiClient(
            transport=httpx.MockTransport(handler), **kwargs
        ),
    )
    session = adapter.start("/tmp/run", "agent", "m")
    for index in range(10):
        adapter.chat(session, f"第 {index} 问" + "内容" * 30, 30_000)

    messages = payloads[-1]["messages"]
    total = sum(len(str(item["content"])) for item in messages)
    assert total <= 200 + 200, f"历史没被裁（共 {total} 字符）"
    assert messages[0]["role"] == "user" and "已省略" in messages[0]["content"], (
        "裁剪后要在最前面留一句说明（静默截断会让模型以为用户只说了这一句）"
    )
    assert messages[-1]["content"].startswith("第 9 问"), "本轮提问必须保留"


# ── 技能 ──────────────────────────────────────────────────────────────


def test_skills_are_discovered_and_injected_into_the_system_prompt(tmp_path):
    """技能是纯文本资产：发现 → 注入系统提示（生成与对话都生效）。"""
    from tu_shell_agent.agent_backends.skills import discover_skills

    root = tmp_path / "skills"
    (root / "quotes").mkdir(parents=True)
    (root / "quotes" / "SKILL.md").write_text(
        "---\nname: quotes\ndescription: 变量一定要加引号\n---\n所有变量展开都要加双引号。\n",
        encoding="utf-8",
    )
    (root / "extra.md").write_text("没有 front matter 也算一个技能。\n", encoding="utf-8")
    (root / "README.md").write_text("这是说明文件，不是技能。\n", encoding="utf-8")
    (root / "empty").mkdir()
    (root / "empty" / "SKILL.md").write_text("   \n", encoding="utf-8")

    skills, problems = discover_skills(root)
    assert [skill.name for skill in skills] == ["extra", "quotes"]
    assert any("empty" in item for item in problems), f"空技能要报出来：{problems}"

    payloads: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        payloads.append(json.loads(request.content.decode("utf-8")))
        return httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            content=_sse(_openai_chunk(content=CONTRACT_REPLY)),
        )

    adapter = _adapter(handler, skills_dir=str(root))
    session = adapter.start("/tmp/run", "agent", "m")
    adapter.generate(session, "写个脚本", {}, 30_000)

    system = payloads[-1]["messages"][0]
    assert system["role"] == "system"
    assert "quotes" in system["content"] and "双引号" in system["content"]
    assert "extra" in system["content"]
    assert "README" not in system["content"], "说明文件不该被当成技能"


def test_only_enabled_skills_are_injected(tmp_path):
    """`enabled_skills` 留空 = 全部；填了名字就只注入那几个。"""
    from tu_shell_agent.agent_backends.skills import compose_system_prompt, select_skills

    root = tmp_path / "skills"
    (root / "a").mkdir(parents=True)
    (root / "a" / "SKILL.md").write_text("甲技能正文\n", encoding="utf-8")
    (root / "b").mkdir()
    (root / "b" / "SKILL.md").write_text("乙技能正文\n", encoding="utf-8")

    from tu_shell_agent.agent_backends.skills import discover_skills

    skills, _ = discover_skills(root)
    assert select_skills(skills, "") == skills
    assert [skill.name for skill in select_skills(skills, "b")] == ["b"]
    assert [skill.name for skill in select_skills(skills, "a，b")] == ["a", "b"], "中文逗号也要认"

    text = compose_system_prompt("基础", select_skills(skills, "b"))
    assert "乙技能正文" in text and "甲技能正文" not in text
    assert compose_system_prompt("基础", []) == "基础", "没有技能时原样返回"


def test_repo_ships_a_shell_skill():
    """仓库自带的 `skills/shell-strict` 要被解析出来（这是"技能"功能的活样例）。"""
    from tu_shell_agent.agent_backends.skills import discover_skills
    from tu_shell_agent.ui.settings import default_skills_dir

    directory = default_skills_dir()
    assert directory.is_dir(), f"仓库里没有技能目录：{directory}"
    skills, problems = discover_skills(directory)
    names = [skill.name for skill in skills]
    assert "shell-strict" in names, f"自带技能没被解析出来：{names}（问题：{problems}）"
    body = next(skill.body for skill in skills if skill.name == "shell-strict")
    assert "set -euo pipefail" in body


def test_builtin_skill_is_written_when_the_directory_is_missing(tmp_path):
    """打包产物里没有 `skills/` 目录：内置技能要**按需落盘**（与内置模板同一套做法）。

    否则 Windows 的 exe 里"技能"这个功能等于不存在 —— 用户装了它却看不到任何技能。
    """
    from tu_shell_agent.agent_backends.skills import (
        BUILTIN_SKILLS,
        discover_skills,
        ensure_builtin_skills,
    )

    target = tmp_path / "userdata" / "skills"
    created = ensure_builtin_skills(target)

    assert created == [skill.name for skill in BUILTIN_SKILLS]
    skills, problems = discover_skills(target)
    assert [skill.name for skill in skills] == created and not problems

    # 再跑一次不该重复写、也不该覆盖用户改过的内容
    first = (target / BUILTIN_SKILLS[0].name / "SKILL.md").read_text(encoding="utf-8")
    (target / BUILTIN_SKILLS[0].name / "SKILL.md").write_text("用户改过的\n", encoding="utf-8")
    assert ensure_builtin_skills(target) == []
    assert (target / BUILTIN_SKILLS[0].name / "SKILL.md").read_text(encoding="utf-8") == "用户改过的\n"
    assert first.strip(), "内置技能正文不该是空的"


def test_repo_skill_file_matches_the_builtin_constant():
    """仓库里那份 `skills/shell-strict/SKILL.md` 与代码里的常量必须一致。

    两份要是漂了，检出台与打包产物会用不同的技能 —— 这种"只在用户机器上才不同"的差异最难查。
    """
    from tu_shell_agent.agent_backends.skills import BUILTIN_SKILLS, parse_skill
    from tu_shell_agent.ui.settings import repo_skills_dir

    root = repo_skills_dir()
    assert root is not None
    text = (root / "shell-strict" / "SKILL.md").read_text(encoding="utf-8")
    parsed = parse_skill(text, fallback_name="shell-strict")
    builtin = next(skill for skill in BUILTIN_SKILLS if skill.name == "shell-strict")

    assert parsed.description == builtin.description
    assert parsed.body.strip() == builtin.body.strip(), "仓库里的技能文件与内置常量漂了"


def test_controller_seeds_skills_into_the_default_dir_when_it_is_missing(qtbot, tmp_path, monkeypatch):
    """控制器解析内置 agent 配置时，默认技能目录不存在就要把内置技能写出来（打包产物的情形）。"""
    from tu_shell_agent.ui import settings as settings_module
    from tu_shell_agent.ui.main_window import MainWindow
    from tu_shell_agent.ui.run_controller import RunController
    from tu_shell_agent.ui.settings import AppSettings

    target = tmp_path / "userdata" / "skills"
    monkeypatch.setattr(settings_module, "default_skills_dir", lambda: target)

    settings = AppSettings(agent_backend="builtin")       # skills_dir 留空 = 用默认目录
    window = MainWindow(wire_controller=False, settings=settings)
    qtbot.addWidget(window)
    controller = RunController(window=window, settings=settings, run_root=str(settings.run_root))

    config = controller._api_config()

    assert config["skills_dir"] == str(target)
    assert (target / "shell-strict" / "SKILL.md").is_file(), "内置技能没有被写出来"


# ── 用户给的那份官方示例：DeepSeek + thinking + 取不到列表时的候选 ──────


def test_provider_presets_fill_address_style_and_candidates(qtbot):
    """选服务商就把**地址 + 风格 + 模型候选**一起填好（这三处最容易填错）。"""
    from tu_shell_agent.agent_backends.backends.builtin import preset_models, suggest_provider
    from tu_shell_agent.ui.pages.settings_page import SettingsPage

    assert suggest_provider("https://api.deepseek.com") == "deepseek"
    assert suggest_provider("https://api.deepseek.com/anthropic") == "deepseek-anthropic"
    assert preset_models("deepseek")[:3] == ["deepseek-chat", "deepseek-reasoner", "deepseek-flash"]

    page = SettingsPage()
    qtbot.addWidget(page)
    page.backend_combo.setCurrentIndex(page.backend_combo.findData("builtin"))
    page.api_provider_combo.setCurrentIndex(page.api_provider_combo.findData("deepseek"))

    assert page.api_base_edit.text() == "https://api.deepseek.com"
    assert page.api_style_combo.currentData() == "openai"
    items = [page.model_combo.itemText(i) for i in range(page.model_combo.count())]
    assert "deepseek-flash" in items, f"候选里没有官方示例用到的模型：{items}"
    assert "gpt-4o" not in items, "内置 agent 的候选不该把别的服务商的模型混进来"


def test_model_candidates_survive_a_failed_list_request(qtbot):
    """取不到真实列表时要给候选 —— 用户要的是"能选一个模型"，不是一句失败原因。"""
    from tu_shell_agent.ui.pages.settings_page import SettingsPage

    page = SettingsPage()
    qtbot.addWidget(page)
    page.backend_combo.setCurrentIndex(page.backend_combo.findData("builtin"))
    page.api_provider_combo.setCurrentIndex(page.api_provider_combo.findData("deepseek"))
    page.model_combo.clear()
    page.model_combo.addItem("", "")

    # 直接走"取列表失败"的那条回调路径
    from tu_shell_agent.ui.engine_worker import ApiModelsWorker

    class _Failing(ApiModelsWorker):
        def __init__(self, api, parent=None):      # noqa: D107 - 替身
            super().__init__(api, parent)

        def start(self):                            # noqa: D102 - 替身：立刻报失败
            self.failed.emit("连不上模型 API（https://api.deepseek.com）")

    monkey = _Failing
    import tu_shell_agent.ui.engine_worker as worker_module

    original = worker_module.ApiModelsWorker
    worker_module.ApiModelsWorker = monkey
    try:
        page._refresh_models()
    finally:
        worker_module.ApiModelsWorker = original

    items = [page.model_combo.itemText(i) for i in range(page.model_combo.count())]
    assert "deepseek-chat" in items, f"失败时没有给候选：{items}"
    assert "候选" in page.model_hint.text()


def test_thinking_flag_lands_in_the_request_body():
    """用户给的官方示例用的是 `reasoning_effort="high"` + `thinking={"type":"enabled"}`。

    打开「深度思考」时这两个字段要真的进请求体（其它时候不加：别的服务商可能不认）。
    """
    payloads: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        payloads.append(json.loads(request.content.decode("utf-8")))
        return httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            content=_sse(_openai_chunk(reasoning="想", content="答")),
        )

    adapter = _adapter(handler, thinking=True)
    session = adapter.start("/tmp/run", "agent", "deepseek-flash")
    adapter.chat(session, "在吗", 30_000)

    assert payloads[-1].get("reasoning_effort") == "high"
    assert payloads[-1].get("thinking") == {"type": "enabled"}

    # 没打开时不许加（否则别的服务商可能直接 400）
    adapter_plain = _adapter(handler, thinking=False)
    session = adapter_plain.start("/tmp/run", "agent", "deepseek-flash")
    adapter_plain.chat(session, "在吗", 30_000)
    assert "thinking" not in payloads[-1] and "reasoning_effort" not in payloads[-1]


def test_proxy_can_be_turned_off(monkeypatch):
    """本机代理不通时，用户必须能关掉它 —— 否则每个请求都卡在"连不上代理"。

    （国内机器上 ALL_PROXY/HTTPS_PROXY 很常见；关掉即直连。）
    """
    captured: dict = {}
    import httpx as httpx_module

    class _FakeClient:
        def __init__(self, **kwargs):
            captured.update(kwargs)

        def close(self):
            pass

    monkeypatch.setattr(httpx_module, "Client", _FakeClient)
    ModelApiClient(base_url="https://api.deepseek.com", api_key="k", use_proxy=False)
    assert captured.get("trust_env") is False, "关掉代理时没有设 trust_env=False"

    ModelApiClient(base_url="https://api.deepseek.com", api_key="k", use_proxy=True)
    assert captured.get("trust_env") is True, "默认应当跟系统代理走"


def test_settings_page_passes_thinking_and_proxy_into_the_api_config(qtbot):
    """设置页把两个勾选框传进 api 配置（不传的话用户勾了也没用）。"""
    from tu_shell_agent.ui.pages.settings_page import SettingsPage

    page = SettingsPage()
    qtbot.addWidget(page)
    page.backend_combo.setCurrentIndex(page.backend_combo.findData("builtin"))
    page.api_thinking_check.setChecked(True)
    page.api_proxy_check.setChecked(False)

    config = page._api_config()
    assert config["thinking"] is True and config["use_proxy"] is False


def test_saved_provider_drives_the_candidates_on_reload(qtbot, tmp_path):
    """打开设置页（不碰下拉）时候选也要来自**已保存的服务商**，不能是所有服务商的并集。

    这条守的是"用户没动过下拉"那条路径：`reload()` → `_apply_backend_model_choices()`。
    把候选来源改成并集时，一个 DeepSeek 用户会在候选里看到 gpt-4o / claude-*。
    """
    from tu_shell_agent.ui.pages.settings_page import SettingsPage
    from tu_shell_agent.ui.settings import AppSettings

    page = SettingsPage()
    qtbot.addWidget(page)
    page.set_settings(
        AppSettings(
            run_root=str(tmp_path / "runs"),
            templates_dir=str(tmp_path / "tpl"),
            agent_backend="builtin",
            api_provider="deepseek",
            api_base="https://api.deepseek.com",
        )
    )

    items = [page.model_combo.itemText(i) for i in range(page.model_combo.count())]
    assert "deepseek-flash" in items
    assert "gpt-4o" not in items and "claude-sonnet-4-5" not in items, (
        f"候选里混进了别的服务商的模型：{items}"
    )


def test_provider_defaults_fill_only_empty_fields(qtbot, tmp_path):
    """打开设置页时只填**空着的**地址；用户手改过的地址不许被预设改回去。

    （踩过：预设下拉默认就停在第一项，用户不重新点一下 → 地址栏是空的。
    现在 reload 时按选中的服务商补默认值，但已保存的值优先。）
    """
    from tu_shell_agent.ui.pages.settings_page import SettingsPage
    from tu_shell_agent.ui.settings import AppSettings

    page = SettingsPage()
    qtbot.addWidget(page)
    page.set_settings(AppSettings(agent_backend="builtin", api_provider="deepseek"))
    page.backend_combo.setCurrentIndex(page.backend_combo.findData("builtin"))
    assert page.api_base_edit.text() == "https://api.deepseek.com", "空地址没有按预设补上"

    # 用户改过地址并**保存**（collect 写回设置对象）：再打开一次不许被预设改回去。
    # 注意：`reload()` 的语义就是"丢弃未保存的编辑"，所以必须先 collect —— 未保存的改动
    # 被回退是设计如此，不是这个用例要保护的东西。
    page.api_base_edit.setText("https://my-proxy.internal/v1")
    page.collect()
    page.reload()
    assert page.api_base_edit.text() == "https://my-proxy.internal/v1", (
        "已保存的地址被服务商预设覆盖了"
    )

    # 「自定义」预设不做任何事
    page.api_provider_combo.setCurrentIndex(page.api_provider_combo.findData("custom"))
    page._apply_provider_defaults(force=True)
    assert page.api_base_edit.text() == "https://my-proxy.internal/v1"


def test_api_settings_round_trip_through_the_settings_file(qtbot, tmp_path):
    """新增的 API 设置（服务商/深度思考/代理/技能）要能存下去、读回来。

    这一条是这类字段的常规事故：界面能勾、保存没写、重启就忘（用户会以为"设置没生效"）。
    """
    from tu_shell_agent.ui.pages.settings_page import SettingsPage
    from tu_shell_agent.ui.settings import AppSettings

    path = tmp_path / "settings.json"
    page = SettingsPage()
    qtbot.addWidget(page)
    page.set_settings(AppSettings.defaults_for(path))
    page.backend_combo.setCurrentIndex(page.backend_combo.findData("builtin"))
    page.api_provider_combo.setCurrentIndex(page.api_provider_combo.findData("deepseek"))
    page._on_api_provider_changed()
    page.api_key_edit.setText("sk-test")
    page.api_thinking_check.setChecked(True)
    page.api_proxy_check.setChecked(False)
    page.enabled_skills_edit.setText("shell-strict")
    saved = page.collect()
    saved.save()

    loaded = AppSettings.load(path)
    assert loaded.agent_backend == "builtin"
    assert loaded.api_provider == "deepseek"
    assert loaded.api_base == "https://api.deepseek.com"
    assert loaded.api_key == "sk-test"
    assert loaded.api_thinking is True
    assert loaded.api_use_proxy is False
    assert loaded.enabled_skills == "shell-strict"


# ── 只读工具：让模型能看运行目录（与 opencode 的 Read/Glob/Grep 对齐）──


def test_readonly_tools_stay_inside_the_run_dir(tmp_path):
    """工具只能读运行目录：`..`、绝对路径、符号链接指向外面、二进制，全部拒绝。

    这是"模型能看文件"这件事的**唯一**边界，必须逐个钉住。
    """
    from tu_shell_agent.agent_backends.readonly_tools import ToolCall, WorkspaceReader

    root = tmp_path / "runs" / "r1"
    (root / "attempts" / "1").mkdir(parents=True)
    (root / "plan.md").write_text("方案正文\n", encoding="utf-8")
    (root / "data.bin").write_bytes(b"\x00\x01" * 40)
    outside = tmp_path / "secret.txt"
    outside.write_text("不该被读到\n", encoding="utf-8")
    link = root / "link.txt"
    try:
        link.symlink_to(outside)
    except OSError:
        link = None

    reader = WorkspaceReader(str(root))

    assert "plan.md" in reader.run(ToolCall("1", "list_dir", {}))
    assert reader.read_file("plan.md") == "方案正文\n"

    for bad in ("../secret.txt", "attempts/../../secret.txt"):
        assert "超出" in reader.run(ToolCall("2", "read_file", {"path": bad})), bad
    # 绝对路径单独拦一次：给它一句**说得清**的错误（而不是笼统的"超出范围"）——
    # 模型看到原因才会改成相对路径；这条断言也把"绝对路径守卫"本身钉住了
    # （两道守卫在行为上重叠，去掉前者时错误信息会变成"超出运行目录"）。
    absolute = reader.run(ToolCall("2b", "read_file", {"path": str(outside)}))
    assert "只接受相对" in absolute, absolute
    if link is not None:
        assert "错误" in reader.run(ToolCall("3", "read_file", {"path": "link.txt"})), (
            "符号链接指向外面也必须拒绝"
        )
    assert "二进制" in reader.run(ToolCall("4", "read_file", {"path": "data.bin"}))
    assert "没有名为" in reader.run(ToolCall("5", "bash", {"cmd": "rm -rf /"}))


def test_tool_read_limits_are_enforced(tmp_path):
    """单文件与目录条目都有上限：不能让模型一次性把上下文灌满。"""
    from tu_shell_agent.agent_backends.readonly_tools import ToolCall, WorkspaceReader

    root = tmp_path / "r"
    root.mkdir()
    (root / "big.txt").write_text("x" * 5000, encoding="utf-8")
    for index in range(20):
        (root / f"f{index:02d}.txt").write_text("y", encoding="utf-8")

    reader = WorkspaceReader(str(root), max_file_bytes=1000, max_entries=5)
    assert "太大" in reader.run(ToolCall("1", "read_file", {"path": "big.txt"}))
    listing = reader.run(ToolCall("2", "list_dir", {}))
    assert "只列了前 5 项" in listing, listing


def _tool_call_chunk(call_id: str, name: str, arguments: str) -> dict:
    return {
        "choices": [
            {
                "delta": {
                    "tool_calls": [
                        {"index": 0, "id": call_id, "function": {"name": name, "arguments": arguments}}
                    ]
                }
            }
        ]
    }


def test_tool_calls_are_parsed_and_executed_then_the_model_answers(tmp_path):
    """完整一轮：模型先要求读文件 → 我们把内容给它 → 它再给出脚本（过契约）。

    这条是"内置 agent 能自己看运行目录"的端到端证据（与 opencode 允许 Read 的口径对齐）。
    """
    root = tmp_path / "r1"
    root.mkdir()
    (root / "plan.md").write_text("把一句话拆成单词逐行打印\n", encoding="utf-8")

    seen: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content.decode("utf-8"))
        seen.append(payload)
        assert payload.get("tools"), "有工具可用时请求里必须带 tools"
        if len(seen) == 1:
            return httpx.Response(
                200,
                headers={"content-type": "text/event-stream"},
                content=_sse(
                    _tool_call_chunk("call_1", "read_file", '{"path": "plan.md"}'),
                    {"choices": [{"delta": {}, "finish_reason": "tool_calls"}]},
                ),
            )
        return httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            content=_sse(_openai_chunk(content=CONTRACT_REPLY)),
        )

    notes: list[str] = []
    adapter = _adapter(handler, note=notes.append)
    session = adapter.start(str(root), "agent", "m")
    script = adapter.generate(session, "按方案写脚本", {}, 30_000)

    assert script.script.strip().startswith("#!/bin/bash")
    # 第二轮请求里必须带上工具结果（模型是看着文件内容写的）
    second = seen[-1]["messages"]
    assert any(item.get("role") == "tool" for item in second), second
    tool_message = next(item for item in second if item.get("role") == "tool")
    assert "把一句话拆成单词逐行打印" in tool_message["content"]
    assert any("plan.md" in note for note in notes), f"界面上应当能看到读了什么：{notes}"


def test_tools_can_be_turned_off(tmp_path):
    """关掉只读工具时，请求里不能带 tools（用户可能就是不希望模型翻目录）。"""
    seen: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(json.loads(request.content.decode("utf-8")))
        return httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            content=_sse(_openai_chunk(content="好")),
        )

    adapter = _adapter(handler, tools=False)
    session = adapter.start(str(tmp_path), "agent", "m")
    adapter.chat(session, "在吗", 30_000)

    assert "tools" not in seen[-1]


def test_tool_loop_stops_at_the_round_limit(tmp_path):
    """模型一直要看更多文件时，循环要在上限处停下（不能无限翻），并且**收回工具**再问最后一次。"""
    from tu_shell_agent.agent_backends.builtin_agent import MAX_TOOL_ROUNDS

    root = tmp_path / "r"
    root.mkdir()
    (root / "a.txt").write_text("甲\n", encoding="utf-8")
    calls = {"n": 0}
    tools_seen: list[bool] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        tools_seen.append("tools" in json.loads(request.content.decode("utf-8")))
        return httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            content=_sse(_tool_call_chunk(f"call_{calls['n']}", "list_dir", "{}")),
        )

    inlets: list[str] = []
    adapter = _adapter(handler, note=inlets.append)
    session = adapter.start(str(root), "agent", "m")
    adapter.chat(session, "看看目录", 30_000)

    assert calls["n"] == MAX_TOOL_ROUNDS + 2, f"工具轮数没在上限处停下：{calls['n']}"
    assert tools_seen[: MAX_TOOL_ROUNDS + 1] == [True] * (MAX_TOOL_ROUNDS + 1)
    assert tools_seen[-1] is False, "最后那一次必须**不带工具**：它就是用来逼出正文的"
    assert any("上限" in note for note in inlets), inlets


def test_last_round_asks_without_tools_so_the_model_must_answer(tmp_path):
    """模型把工具轮数花光了也不肯写正文时，最后一次**不给工具**，它就只能说话。

    真实全链路冒烟抓到过这个：模型一直 list_dir / read_file 到上限，最后一轮只回了
    tool_calls、正文是空的 → 上层报"模型没有返回任何文本"，而真正的原因（预算花在翻文件上）
    从消息里完全看不出来。修法就是收回工具再问一次 —— 这条用例钉住"收回了、且拿到了正文"。
    """
    from tu_shell_agent.agent_backends.builtin_agent import MAX_TOOL_ROUNDS

    root = tmp_path / "r"
    root.mkdir()
    (root / "a.txt").write_text("甲\n", encoding="utf-8")
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] <= MAX_TOOL_ROUNDS + 1:      # 前面每一轮都只想看文件
            return httpx.Response(
                200,
                headers={"content-type": "text/event-stream"},
                content=_sse(_tool_call_chunk(f"call_{calls['n']}", "list_dir", "{}")),
            )
        # 收回工具之后它终于说话了
        return httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            content=_sse(_openai_chunk(content="看完了，这就是答案。")),
        )

    adapter = _adapter(handler)
    session = adapter.start(str(root), "agent", "m")
    reply = adapter.chat(session, "看看目录", 30_000)

    assert reply == "看完了，这就是答案。", f"没有拿到正文：{reply!r}"
    assert calls["n"] == MAX_TOOL_ROUNDS + 2


def test_anthropic_style_tool_calls_are_parsed(tmp_path):
    """Anthropic 风格的 tool_use / tool_result 形状也要走通。"""
    root = tmp_path / "r"
    root.mkdir()
    (root / "plan.md").write_text("方案\n", encoding="utf-8")
    seen: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content.decode("utf-8"))
        seen.append(payload)
        if len(seen) == 1:
            return httpx.Response(
                200,
                headers={"content-type": "text/event-stream"},
                content=_sse(
                    {
                        "type": "content_block_start",
                        "index": 0,
                        "content_block": {"type": "tool_use", "id": "toolu_1", "name": "read_file"},
                    },
                    {
                        "type": "content_block_delta",
                        "index": 0,
                        "delta": {"type": "input_json_delta", "partial_json": '{"path": "plan.md"}'},
                    },
                    {"type": "message_delta", "delta": {"stop_reason": "tool_use"}},
                ),
            )
        return httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            content=_sse({"type": "content_block_delta", "delta": {"type": "text_delta", "text": "好"}}),
        )

    adapter = BuiltinAdapter(
        base_url="https://api.anthropic.com",
        api_key="k",
        style="anthropic",
        model="claude-sonnet-4-5",
        client_factory=lambda **kwargs: ModelApiClient(
            transport=httpx.MockTransport(handler), **kwargs
        ),
    )
    session = adapter.start(str(root), "agent", "claude-sonnet-4-5")
    reply = adapter.chat(session, "看看方案", 30_000)

    assert reply == "好"
    # 第二轮的 user 消息里应当是 tool_result 块
    blocks = seen[-1]["messages"][-1]["content"]
    assert isinstance(blocks, list) and blocks[0]["type"] == "tool_result"
    assert "方案" in blocks[0]["content"]


# ── 输出长度：停止原因必须收下来（真实冒烟抓到过"被截断却看不出原因"）────


def test_stop_reason_is_captured_from_both_styles():
    """上游说"我被长度截断了"时要把这句话收下来 —— 之前这个字段**根本没解析**。

    OpenAI 兼容把它放在 `choices[0].finish_reason`，Anthropic 兼容放在
    `message_delta.delta.stop_reason`。不收的后果：真实全链路冒烟里那一次回复被
    `max_tokens` 从中间切断，界面上只看到"契约标记缺了最后两段"，看不出是被长度砍的。
    """
    from tu_shell_agent.agent_backends.api_client import was_truncated

    def openai_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            content=_sse(
                _openai_chunk(content="正文"),
                {"choices": [{"delta": {}, "finish_reason": "length"}]},
            ),
        )

    completion = _client(openai_handler).stream_chat(
        model="m", messages=[{"role": "user", "content": "x"}]
    )
    assert completion.finish_reason == "length", completion.finish_reason
    assert was_truncated(completion) is True

    def anthropic_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            content=_sse(
                {"type": "content_block_delta", "delta": {"type": "text_delta", "text": "正文"}},
                {"type": "message_delta", "delta": {"stop_reason": "max_tokens"}},
            ),
        )

    completion = ModelApiClient(
        base_url="https://api.anthropic.com",
        api_key="k",
        style="anthropic",
        transport=httpx.MockTransport(anthropic_handler),
    ).stream_chat(model="m", messages=[{"role": "user", "content": "x"}])
    assert completion.finish_reason == "max_tokens", completion.finish_reason
    assert was_truncated(completion) is True

    # 正常说完不该被当成截断
    plain = _client(lambda request: httpx.Response(
        200,
        headers={"content-type": "text/event-stream"},
        content=_sse(_openai_chunk(content="正文"), {"choices": [{"delta": {}, "finish_reason": "stop"}]}),
    )).stream_chat(model="m", messages=[{"role": "user", "content": "x"}])
    assert was_truncated(plain) is False


def test_thinking_gets_a_larger_output_budget():
    """开了「深度思考」要放大 max_tokens：思考过程与正文**共用**同一个输出预算。

    实测（真实冒烟）：`max_tokens=4096` + thinking，上游回
    `stop_reason=max_tokens, output_tokens=4096` —— 正文被从中间切断。
    """
    from tu_shell_agent.agent_backends.api_client import DEFAULT_MAX_TOKENS, THINKING_MAX_TOKENS

    seen: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(int(json.loads(request.content.decode("utf-8"))["max_tokens"]))
        return httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            content=_sse(_openai_chunk(content="答")),
        )

    assert THINKING_MAX_TOKENS > DEFAULT_MAX_TOKENS, "深度思考那一档必须更大，否则放大没意义"

    adapter = _adapter(handler, thinking=True)
    session = adapter.start("/tmp/run", "agent", "m")
    adapter.chat(session, "在吗", 30_000)
    assert seen[-1] == THINKING_MAX_TOKENS

    adapter = _adapter(handler, thinking=False)
    session = adapter.start("/tmp/run", "agent", "m")
    adapter.chat(session, "在吗", 30_000)
    assert seen[-1] == DEFAULT_MAX_TOKENS, "没开深度思考时不该跟着放大"


def test_truncated_reply_is_reported_in_the_ui():
    """被截断时要在界面上说一句（"上限 4096 tokens、正在回灌重试"），不能悄悄少一段。"""
    notes: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            content=_sse(
                _openai_chunk(content="===TU-SCRIPT===\n#!/bin/bash\n"),
                {"choices": [{"delta": {}, "finish_reason": "length"}]},
            ),
        )

    adapter = _adapter(handler, note=notes.append)
    session = adapter.start("/tmp/run", "agent", "m")
    with pytest.raises(RuntimeError):
        adapter.generate(session, "写个脚本", {}, 30_000)

    joined = "\n".join(notes)
    assert "截断" in joined, notes
    assert "finish_reason=length" in joined, f"要写清上游给的原因：{notes}"
    assert "4096" in joined, "要写出上限是多少（用户才知道往哪儿调）"
    assert "深度思考" in joined, "要告诉用户下一步能关掉深度思考"
