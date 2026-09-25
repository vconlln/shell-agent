"""直连模型 API 的最小客户端：OpenAI 兼容与 Anthropic 兼容两家，都走流式。

**为什么要有它**（用户要求："我要做的是自己的 agent，你也可以自己做 agent 不用调用别的后端"）：
调用别人的 CLI（opencode / claude / codeagent）有两个躲不开的代价 ——

1. **慢**：每轮对话都要起一个 node 进程（冷启动几秒），首字延迟明显；用户实测"输入问题要停顿
   很久才有回复"；
2. **不透明**：思考过程（reasoning）被 CLI 吞掉或者只以它自己的格式吐出来，界面上看不到。

直连 API 之后这两条都没了：一个 HTTP 请求、SSE 流式返回，`reasoning_content` / `thinking_delta`
也能逐字显示出来。

支持的两种风格（都是"厂商自己的 HTTP 接口"，不是别人的 CLI）：

| 风格 | 地址 | 请求体 | 流里的增量 |
| --- | --- | --- | --- |
| `openai`（默认，覆盖 DeepSeek / OpenAI / 本地 vLLM、Ollama、LM Studio 等） | `{base}/chat/completions` | `messages[]` | `choices[0].delta.content` / `.reasoning_content` |
| `anthropic` | `{base}/v1/messages`（base 已含 /v1 时不重复加） | `messages[]` + `system` | `content_block_delta` 的 `text_delta` / `thinking_delta` |

**流式解析要抗得住脏数据**：SSE 里会出现空行、注释行（`:` 开头）、`[DONE]` 哨兵、
以及厂商自己插的 `event:` 行；任何一行都**不许**让整轮崩掉（CLI 那边已经有同样的要求：
"解析器不能比上游更脆"）。
"""

from __future__ import annotations

import json
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from typing import Any

import httpx

# 单次响应的默认上限：生成/对话都是"一个字一个字回来"，超时按"两次数据之间的间隔"算更合适，
# 但 httpx 只支持整请求超时，所以给一个宽松的值（用户可在设置里改生成超时）。
DEFAULT_TIMEOUT_S = 300.0

# 「列模型 / 检测」这类请求必须**短**：它只是问一句"能不能用"，不该让界面停在
# "正在向模型 API 获取可用模型…"上五分钟（用户实测就是这样：一直显示正在获取）。
LIST_TIMEOUT_S = 20.0

# 思考过程与正文的分界标记：界面上要把"模型在想什么"和"模型给出的答案"分开显示，
# 而 on_delta 只传文本（协议如此），所以用两行标题把它们分开。
THINKING_HEADER = "—— 思考过程 ——"
ANSWER_HEADER = "—— 回复 ——"


class ApiError(RuntimeError):
    """网络/协议/权限错误，消息直接可以显示给用户（含"该怎么办"）。"""


@dataclass(slots=True)
class StreamEvent:
    """流里的一段文本：`kind` 区分思考与正文。"""

    kind: str          # "reasoning" | "content"
    text: str


@dataclass(slots=True)
class Completion:
    """一次调用的结果（含模型请求的工具调用）。"""

    content: str = ""
    reasoning: str = ""
    model: str = ""
    finish_reason: str = ""
    raw_events: int = field(default=0)
    # 模型请求的工具调用（OpenAI 的 tool_calls / Anthropic 的 tool_use）。
    # 形状统一成 `readonly_tools.ToolCall`，两种风格在这里合流。
    tool_calls: list[Any] = field(default_factory=list)


def _headers(style: str, api_key: str, extra: dict[str, str] | None = None) -> dict[str, str]:
    headers = {"Content-Type": "application/json", "Accept": "text/event-stream"}
    if api_key:
        if style == "anthropic":
            headers["x-api-key"] = api_key
            headers["anthropic-version"] = "2023-06-01"
        else:
            headers["Authorization"] = f"Bearer {api_key}"
    if extra:
        headers.update(extra)
    return headers


def chat_completions_url(base_url: str, style: str) -> str:
    """把用户填的 base 拼成真正的接口地址（容错：结尾斜杠、已含路径、已含 /v1）。"""
    base = (base_url or "").strip().rstrip("/")
    if not base:
        raise ApiError("没有填写 API 地址（例如 https://api.deepseek.com/v1）")
    if style == "anthropic":
        if base.endswith("/messages"):
            return base
        if base.endswith("/v1"):
            return f"{base}/messages"
        return f"{base}/v1/messages"
    if base.endswith("/chat/completions"):
        return base
    if base.endswith("/v1"):
        return f"{base}/chat/completions"
    return f"{base}/chat/completions"


def models_url(base_url: str, style: str) -> str:
    """列出模型的地址（用于「检测可用模型」）。"""
    base = (base_url or "").strip().rstrip("/")
    if not base:
        raise ApiError("没有填写 API 地址")
    if base.endswith("/models"):
        return base
    if base.endswith("/v1"):
        return f"{base}/models"
    if style == "anthropic":
        return f"{base}/v1/models"
    return f"{base}/models"


def models_url_candidates(base_url: str, style: str) -> list[str]:
    """列模型时依次尝试的地址（去重、保持顺序）。

    为什么要多个：兼容端点常常只实现"对话"那一条。用户实测的
    `https://api.deepseek.com/anthropic` 就是 Anthropic 兼容的**对话**端点，
    它没有 `/v1/models`；而同一站点的 OpenAI 兼容根地址 `https://api.deepseek.com/models`
    是有的 —— 所以这里按"风格自己的地址 → 站点根上的两种常见写法"依次试。
    """
    candidates = [models_url(base_url, style)]
    base = (base_url or "").strip().rstrip("/")
    if base:
        root = base
        for suffix in ("/anthropic", "/v1", "/openai"):
            if root.endswith(suffix):
                root = root[: -len(suffix)]
                break
        for extra in (f"{root}/models", f"{root}/v1/models"):
            if extra not in candidates:
                candidates.append(extra)
    return candidates


def _model_names(payload: Any) -> list[str]:
    """从模型列表响应里取名字（OpenAI 是 `data[].id`，Anthropic 也是 `data[].id`）。"""
    items = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(items, list):
        return []
    names: list[str] = []
    for item in items:
        if isinstance(item, dict):
            name = item.get("id") or item.get("name")
        else:
            name = item
        text = str(name or "").strip()
        if text and text not in names:
            names.append(text)
    return names


def _error_message(response: httpx.Response, base_url: str) -> str:
    """把状态码翻译成"该怎么办" —— 只说"HTTP 401"没人知道要做什么。"""
    status = response.status_code
    detail = ""
    try:
        payload = response.json()
        if isinstance(payload, dict):
            error = payload.get("error")
            if isinstance(error, dict):
                detail = str(error.get("message") or "")
            elif error:
                detail = str(error)
            detail = detail or str(payload.get("message") or "")
    except (ValueError, json.JSONDecodeError):
        detail = (response.text or "").strip()[:200]
    hints = {
        401: "API key 不对或没带上：检查「设置 → 内置 agent」里的 API key。",
        403: "这个 key 没有访问该模型的权限（或在当前地区不可用）。",
        404: "地址或模型名不对：确认 API 地址（含 /v1）与模型名。",
        429: "触发限流或余额不足：稍后再试，或换一个模型。",
    }
    hint = hints.get(status, "检查 API 地址、key 与模型名。")
    tail = f"：{detail}" if detail else ""
    return f"模型 API 返回 HTTP {status}{tail}\n{hint}（地址：{base_url}）"


def _iter_sse_lines(response: httpx.Response) -> Iterator[tuple[str, str]]:
    """把 SSE 响应拆成 (event, data) 对；脏行一律跳过。"""
    event = ""
    for raw in response.iter_lines():
        line = raw if isinstance(raw, str) else raw.decode("utf-8", "replace")
        line = line.rstrip("\r")
        if not line:
            event = ""
            continue
        if line.startswith(":"):          # SSE 注释/心跳
            continue
        if line.startswith("event:"):
            event = line[len("event:") :].strip()
            continue
        if not line.startswith("data:"):
            continue
        data = line[len("data:") :].strip()
        if not data or data == "[DONE]":
            continue
        yield event, data


def _openai_tool_calls(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """取这一帧里的 tool_calls 分片（OpenAI 是**分片累积**：index 相同就拼 arguments）。"""
    choices = payload.get("choices")
    if not isinstance(choices, list) or not choices:
        return []
    choice = choices[0]
    if not isinstance(choice, dict):
        return []
    delta = choice.get("delta")
    if not isinstance(delta, dict):
        return []
    calls = delta.get("tool_calls")
    return [item for item in calls if isinstance(item, dict)] if isinstance(calls, list) else []


def _merge_openai_tool_calls(buffer: dict[int, dict[str, Any]], chunks: list[dict[str, Any]]) -> None:
    for chunk in chunks:
        index = int(chunk.get("index") or 0)
        slot = buffer.setdefault(index, {"id": "", "name": "", "arguments": ""})
        if chunk.get("id"):
            slot["id"] = str(chunk["id"])
        function = chunk.get("function")
        if isinstance(function, dict):
            if function.get("name"):
                slot["name"] = str(function["name"])
            if isinstance(function.get("arguments"), str):
                slot["arguments"] += function["arguments"]


def _finish_openai_tool_calls(buffer: dict[int, dict[str, Any]]) -> list[Any]:
    """把累积的分片变成 `ToolCall`（arguments 是 JSON 字符串，解析失败就给空参数）。"""
    from .readonly_tools import ToolCall

    calls: list[Any] = []
    for index in sorted(buffer):
        slot = buffer[index]
        name = str(slot.get("name") or "").strip()
        if not name:
            continue
        raw = str(slot.get("arguments") or "").strip() or "{}"
        try:
            arguments = json.loads(raw)
        except json.JSONDecodeError:
            arguments = {}
        if not isinstance(arguments, dict):
            arguments = {}
        calls.append(
            ToolCall(id=str(slot.get("id") or f"call_{index}"), name=name, arguments=arguments)
        )
    return calls


def _parse_openai_chunk(payload: dict[str, Any]) -> StreamEvent | None:
    """OpenAI 兼容流里的一帧 → 文本片段（认不出来返回 None）。"""
    choices = payload.get("choices")
    if not isinstance(choices, list) or not choices:
        return None
    choice = choices[0]
    if not isinstance(choice, dict):
        return None
    delta = choice.get("delta")
    if not isinstance(delta, dict):
        # 非流式的整段回复（有些兼容实现只在最后一帧给 message）
        message = choice.get("message")
        if isinstance(message, dict) and isinstance(message.get("content"), str):
            return StreamEvent("content", message["content"])
        return None
    reasoning = delta.get("reasoning_content") or delta.get("reasoning")
    if isinstance(reasoning, str) and reasoning:
        return StreamEvent("reasoning", reasoning)
    content = delta.get("content")
    if isinstance(content, str) and content:
        return StreamEvent("content", content)
    return None


def _parse_anthropic_chunk(payload: dict[str, Any]) -> StreamEvent | None:
    """Anthropic 兼容流里的一帧 → 文本片段。"""
    if payload.get("type") != "content_block_delta":
        return None
    delta = payload.get("delta")
    if not isinstance(delta, dict):
        return None
    if isinstance(delta.get("thinking"), str) and delta["thinking"]:
        return StreamEvent("reasoning", delta["thinking"])
    text = delta.get("text")
    if isinstance(text, str) and text:
        return StreamEvent("content", text)
    return None


def _anthropic_tool_calls(events: list[dict[str, Any]]) -> list[Any]:
    """从 Anthropic 流里收集 tool_use 块（`content_block_start` 起块，`input_json_delta` 攒 JSON）。"""
    from .readonly_tools import ToolCall

    blocks: dict[int, dict[str, Any]] = {}
    for event in events:
        kind = event.get("type")
        if kind == "content_block_start":
            block = event.get("content_block")
            if isinstance(block, dict) and block.get("type") == "tool_use":
                blocks[int(event.get("index") or 0)] = {
                    "id": str(block.get("id") or ""),
                    "name": str(block.get("name") or ""),
                    "json": "",
                }
        elif kind == "content_block_delta":
            delta = event.get("delta")
            if isinstance(delta, dict) and isinstance(delta.get("partial_json"), str):
                slot = blocks.get(int(event.get("index") or 0))
                if slot is not None:
                    slot["json"] += delta["partial_json"]
    calls: list[Any] = []
    for index in sorted(blocks):
        slot = blocks[index]
        if not slot["name"]:
            continue
        try:
            arguments = json.loads(slot["json"] or "{}")
        except json.JSONDecodeError:
            arguments = {}
        calls.append(
            ToolCall(
                id=slot["id"] or f"toolu_{index}",
                name=slot["name"],
                arguments=arguments if isinstance(arguments, dict) else {},
            )
        )
    return calls


class ModelApiClient:
    """一次配置对应一个客户端（base / key / style）。线程安全：显式 cancel 用事件。"""

    def __init__(
        self,
        *,
        base_url: str,
        api_key: str = "",
        style: str = "openai",
        timeout_s: float = DEFAULT_TIMEOUT_S,
        transport: httpx.BaseTransport | None = None,
        use_proxy: bool = True,
    ) -> None:
        self.base_url = (base_url or "").strip()
        self.api_key = api_key or ""
        self.style = "anthropic" if style == "anthropic" else "openai"
        self.timeout_s = timeout_s
        self.use_proxy = bool(use_proxy)
        try:
            self._client = httpx.Client(
                # 代理：默认跟系统走（用户在国内访问 OpenAI/Anthropic 往往必须走代理），
                # 但设置里可以**关掉**：本机代理挂掉时，所有请求都会卡在连不上代理上，
                # 而用户只会看到"连不上模型 API"。关掉 = 直连。
                trust_env=self.use_proxy,
                timeout=httpx.Timeout(timeout_s, connect=15.0),
                # 代理：这里**不**继承系统代理设置里的 loopback 例外表 —— 直连的是公网 API，
                # 用户配了代理就该走代理（与本机 opencode serve 那条"必须绕过代理"的规则相反）。
                transport=transport,
            )
        except ImportError as error:
            # 国内机器上很常见：环境里配了 SOCKS 代理（ALL_PROXY=socks5://…），
            # 而 httpx 需要 socksio 才能用。报清楚"要么装、要么别走 SOCKS"，
            # 否则用户看到的是 `Using SOCKS proxy, but the 'socksio' package is not installed`
            # 这种只在 Python 圈里说得通的话。
            raise ApiError(
                "检测到系统代理是 SOCKS，但缺少 socksio 依赖："
                "请安装 `httpx[socks]`（本应用已声明该依赖，重装即可），"
                "或临时清掉 ALL_PROXY / HTTPS_PROXY 环境变量。"
                f"（原始错误：{error}）"
            ) from error
        except httpx.HTTPError as error:  # 代理地址写错等
            raise ApiError(f"创建 HTTP 客户端失败：{error}") from error

    # ── 对外 ──────────────────────────────────────────────────────────
    def list_models(self, *, timeout_s: float = LIST_TIMEOUT_S) -> list[str]:
        """列出可用模型（「检测可用模型」与对话面板共用）。失败抛 `ApiError`。

        会按候选地址**依次试**（见 `models_url_candidates`）：很多服务商只有一个兼容端点
        （例如 DeepSeek 的 `https://api.deepseek.com/anthropic` 是 Anthropic 兼容的对话端点，
        它**没有** `/v1/models`），这时候退回同一站点的 OpenAI 兼容 `/models` 就能列到；
        全试完还失败，就把试过哪些地址一起报出来（用户能自己判断该填什么）。
        """
        candidates = models_url_candidates(self.base_url, self.style)
        errors: list[str] = []
        for url in candidates:
            try:
                response = self._client.get(
                    url,
                    headers=_headers(
                        self.style, self.api_key, {"Accept": "application/json"}
                    ),
                    timeout=timeout_s,
                )
            except httpx.TimeoutException:
                errors.append(
                    f"{url} 超过 {timeout_s:.0f} 秒没有响应（检查网络、代理或地址）"
                )
                continue
            except httpx.HTTPError as error:
                errors.append(f"{url} 连不上：{error}")
                continue
            if response.status_code in (404, 405):
                errors.append(f"{url} 返回 HTTP {response.status_code}（该地址没有模型列表）")
                continue
            if response.status_code >= 400:
                # 401/403 这类是"配置不对"，换地址也没用 —— 直接把它报出来
                raise ApiError(_error_message(response, url))
            try:
                payload = response.json()
            except ValueError as error:
                errors.append(f"{url} 返回的不是 JSON：{error}")
                continue
            return _model_names(payload)
        raise ApiError("没有取到模型列表，已尝试：" + "；".join(errors))
        return _model_names(payload)

    def stream_chat(
        self,
        *,
        model: str,
        messages: list[dict[str, str]],
        system: str = "",
        cancel: Any = None,
        on_event: Callable[[StreamEvent], None] | None = None,
        max_tokens: int = 4096,
        timeout_s: float | None = None,
        retries: int = 2,
        thinking: bool = False,
        tools: list[dict[str, Any]] | None = None,
    ) -> Completion:
        """一次流式对话；`on_event` 逐段回调（思考与正文分开），返回汇总结果。

        - `timeout_s` 由调用方给（引擎传的是本轮生成/对话的超时），不传就用客户端默认值；
        - `retries`：**还没吐字**时遇到瞬时故障（连不上、读超时、429/5xx）会重试，
          指数退避。已经收到内容的流**不重试** —— 那样会把同一段答案重复写进对话记录。
        `cancel` 可以是 threading.Event（`.is_set()`）/ 可调用对象 / None —— 与引擎那边
        的取消原语保持一致（见 shell_toolchain.execute 的用法）。
        """
        attempt = 0
        while True:
            try:
                return self._stream_once(
                    model=model,
                    messages=messages,
                    system=system,
                    cancel=cancel,
                    on_event=on_event,
                    max_tokens=max_tokens,
                    timeout_s=timeout_s,
                    thinking=thinking,
                    tools=tools,
                )
            except ApiError as error:
                if not _retryable(error) or attempt >= max(retries, 0) or _cancelled(cancel):
                    raise
                attempt += 1
                self._sleep(0.5 * (2 ** (attempt - 1)), cancel)

    def _stream_once(
        self,
        *,
        model: str,
        messages: list[dict[str, str]],
        system: str,
        cancel: Any,
        on_event: Callable[[StreamEvent], None] | None,
        max_tokens: int,
        timeout_s: float | None,
        thinking: bool = False,
        tools: list[dict[str, Any]] | None = None,
    ) -> Completion:
        """真正发一次请求（重试逻辑在 `stream_chat` 里）。"""
        url = chat_completions_url(self.base_url, self.style)
        if self.style == "anthropic":
            payload: dict[str, Any] = {
                "model": model,
                "messages": messages,
                "max_tokens": max_tokens,
                "stream": True,
            }
            if thinking:
                # Anthropic 风格的思考开关是 `thinking`（budget_tokens 给足才会真的思考）
                payload["thinking"] = {"type": "enabled", "budget_tokens": 4096}
            if tools:
                payload["tools"] = tools
            if system.strip():
                payload["system"] = system
        else:
            payload = {
                "model": model,
                "messages": ([{"role": "system", "content": system}] if system.strip() else [])
                + messages,
                "stream": True,
            }
            if thinking:
                # 用户给的官方示例就是这么写的（`reasoning_effort="high"` +
                # `extra_body={"thinking": {"type": "enabled"}}`）：把它原样落进请求体。
                # 只有用户显式打开「深度思考」时才加 —— 别的服务商可能不认这两个字段。
                payload["reasoning_effort"] = "high"
                payload["thinking"] = {"type": "enabled"}
            if tools:
                payload["tools"] = tools
        result = Completion(model=model)
        request_timeout = (
            httpx.Timeout(timeout_s, connect=15.0) if timeout_s else None
        )
        try:
            with self._client.stream(
                "POST",
                url,
                headers=_headers(self.style, self.api_key),
                json=payload,
                timeout=request_timeout,
            ) as response:
                if response.status_code >= 400:
                    response.read()
                    error = ApiError(_error_message(response, self.base_url))
                    error.status = response.status_code        # 供重试判断用
                    raise error
                tool_buffer: dict[int, dict[str, Any]] = {}
                anthropic_events: list[dict[str, Any]] = []
                for event_name, data in _iter_sse_lines(response):
                    if _cancelled(cancel):
                        result.finish_reason = result.finish_reason or "cancelled"
                        break
                    try:
                        event_payload = json.loads(data)
                    except json.JSONDecodeError:
                        continue
                    if not isinstance(event_payload, dict):
                        continue
                    result.raw_events += 1
                    if self.style != "anthropic":
                        _merge_openai_tool_calls(tool_buffer, _openai_tool_calls(event_payload))
                    else:
                        anthropic_events.append(event_payload)
                    parsed = (
                        _parse_anthropic_chunk(event_payload)
                        if self.style == "anthropic" or event_name
                        else _parse_openai_chunk(event_payload)
                    )
                    if parsed is None:
                        # 有的兼容实现不写 event: 行，这里两种解析都试一次
                        parsed = (
                            _parse_openai_chunk(event_payload)
                            if self.style != "anthropic"
                            else _parse_anthropic_chunk(event_payload)
                        )
                    if parsed is None:
                        continue
                    if parsed.kind == "reasoning":
                        result.reasoning += parsed.text
                    else:
                        result.content += parsed.text
                    if on_event is not None:
                        on_event(parsed)
        except httpx.HTTPError as error:
            if _cancelled(cancel):
                result.finish_reason = "cancelled"
                return result
            raise ApiError(f"连不上模型 API（{self.base_url}）：{error}") from error
        if self.style == "anthropic":
            result.tool_calls = _anthropic_tool_calls(anthropic_events)
        else:
            result.tool_calls = _finish_openai_tool_calls(tool_buffer)
        return result

    def _sleep(self, seconds: float, cancel: Any) -> None:
        """退避等待；被取消就别等完（< 1 秒的等待也要能被打断）。"""
        import time

        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            if _cancelled(cancel):
                return
            time.sleep(0.05)

    def close(self) -> None:
        self._client.close()


# 哪些错误值得重试：连接问题、限流与服务端错误。**401/403/404 不重试** ——
# 那是"配置不对"，重试只是把同样的失败重复几遍、还让用户多等十几秒。
_RETRYABLE_STATUS = {408, 409, 425, 429, 500, 502, 503, 504}


def _retryable(error: ApiError) -> bool:
    status = getattr(error, "status", None)
    if isinstance(status, int):
        return status in _RETRYABLE_STATUS
    text = str(error)
    return "连不上模型 API" in text or "没有响应" in text


def _cancelled(cancel: Any) -> bool:
    if cancel is None:
        return False
    if callable(cancel):
        try:
            return bool(cancel())
        except Exception:  # noqa: BLE001 - 取消原语五花八门，问不出来就当没取消
            return False
    checker = getattr(cancel, "is_set", None)
    if callable(checker):
        return bool(checker())
    return False


def delta_stream(
    on_event: Callable[[StreamEvent], None] | None,
    on_delta: Callable[[str], None] | None,
) -> Callable[[StreamEvent], None] | None:
    """把"分段事件"适配成界面要的 `on_delta(text)`，并在思考/正文之间插入标题行。

    做法：状态机只关心"当前处在哪一种片段里"，切换时先发一行标题 —— 于是对话记录里长这样：

        —— 思考过程 ——
        （模型的推理，逐字出现）
        —— 回复 ——
        （最终答案）

    这样用户能直接看到"模型在想什么"（用户明确要求："不像现在这样我输入问题有思考过程什么的"）。
    """
    if on_delta is None and on_event is None:
        return None
    state = {"kind": ""}

    def handle(event: StreamEvent) -> None:
        if on_event is not None:
            on_event(event)
        if on_delta is None:
            return
        if event.kind != state["kind"]:
            state["kind"] = event.kind
            header = THINKING_HEADER if event.kind == "reasoning" else ANSWER_HEADER
            on_delta(f"\n{header}\n")
        on_delta(event.text)

    return handle
