"""内置 agent：**直连模型 API**，不经过任何别人的 CLI。

用户要求："我要做的是自己的 agent，你也可以自己做 agent 不用调用别的后端"。这个适配器就是
那条路：它实现与 opencode / CLI 后端**完全相同的端口**（`OpencodePort`），于是编排层、界面、
权限模型一行都不用改 —— 换的只是"谁来生成文本"。

## 安全模型不变（这一点必须写在最前面）

- 它**没有任何工具**：不会执行命令、不读不写文件、不联网（除了调用模型 API 本身）。
  它的产物只有文本；脚本由 `orchestrator` 从文本里按契约解析出来、写盘、shellcheck、
  经人工确认后才执行 —— 引擎仍然是唯一的执行者，这一层比 CLI 后端**更窄**（CLI 那边还得靠
  `--disallowedTools` 去收敛工具）。
- `chat` 与 `generate` 的区别与其它后端逐字一致：`chat` 只是说话，产出的脚本不会自动执行。

## 为什么它更快

CLI 后端每轮都要起一个 node 进程（冷启动几秒）+ 让 CLI 自己去建会话；这里是**一个 HTTP 请求
流式返回**，首字延迟只有模型自己的首 token 时间。而且 `reasoning_content` / `thinking_delta`
能逐字显示 —— 用户能直接看到"模型在想什么"。

## 会话与历史

会话 id 由本适配器生成（uuid），历史保存在**运行目录**（`chat.jsonl`，与界面回填用的是同一份
文件），`resume(run_dir)` 时读回来 —— 于是"重启应用接着上次聊"这件事不依赖任何外部服务。
"""

from __future__ import annotations

import threading
import uuid
from collections.abc import Callable
from typing import Any

from ..orchestrator.cli_contract import (
    CLI_SYSTEM_RULES,
    CliContractError,
    parse_generated_script,
    with_output_instructions,
)
from ..types import GeneratedScript
from .api_client import ApiError, ModelApiClient, delta_stream

DEFAULT_SYSTEM_RULES = CLI_SYSTEM_RULES

# 生成脚本时给模型的系统提示：与 CLI 后端同一份硬规则（"引擎是唯一执行者"那一套）。
GENERATE_SYSTEM_PROMPT = CLI_SYSTEM_RULES


class BuiltinAdapter:
    """直连模型 API 的适配器（`OpencodePort` 的第三种实现）。"""

    def __init__(
        self,
        *,
        base_url: str,
        api_key: str = "",
        style: str = "openai",
        model: str = "",
        note: Callable[[str], None] | None = None,
        client_factory: Callable[..., ModelApiClient] | None = None,
    ) -> None:
        self._base_url = base_url
        self._api_key = api_key
        self._style = style
        self._default_model = model
        self._note = note or (lambda _message: None)
        self._client_factory = client_factory or ModelApiClient
        self._client: ModelApiClient | None = None
        self._lock = threading.Lock()
        self._run_dir = ""
        self._session_id = ""
        self._model = model
        # 会话历史：内存里维护，落盘在运行目录（chat.jsonl），resume 时读回来
        self._messages: list[dict[str, str]] = []
        self._cancel = threading.Event()

    # ── 端口：会话 ────────────────────────────────────────────────────
    def start(self, run_dir: str, agent_name: str = "", model: str | None = None) -> str:
        """建一段新会话：生成 id、清空历史、记住运行目录与模型。"""
        del agent_name
        self._run_dir = str(run_dir or self._run_dir)
        if model:
            self._model = str(model)
        self._session_id = str(uuid.uuid4())
        self._messages = []
        self._cancel.clear()
        return self._session_id

    def resume(self, run_dir: str, model: str | None = None) -> None:
        """接着上次聊：把历史从运行目录读回来（`chat.jsonl` 与界面回填的是同一份）。"""
        if run_dir:
            self._run_dir = str(run_dir)
        if model:
            self._model = str(model)
        self._messages = self._load_history()
        self._cancel.clear()

    def abort(self, session_id: str) -> None:
        """取消当前这一轮：置位取消事件，正在读的流会在下一个数据块处停下。"""
        del session_id
        self._cancel.set()

    def dispose(self) -> None:
        """收尾：取消在飞的一轮并关掉 HTTP 客户端（不抛异常，它出现在 finally 里）。"""
        self._cancel.set()
        with self._lock:
            client, self._client = self._client, None
        if client is not None:
            try:
                client.close()
            except Exception:  # noqa: BLE001 - 关掉失败不该影响退出
                pass

    # ── 端口：生成与对话 ──────────────────────────────────────────────
    def generate(
        self,
        session_id: str,
        message: str,
        schema: dict[str, Any],
        timeout_ms: int,
        on_delta: Callable[[str], None] | None = None,
        cancel: Any = None,
    ) -> GeneratedScript:
        """一轮生成：与 CLI 后端**同一套文本契约**（`cli_contract`），所以解析逻辑完全一致。

        `schema` 被忽略：这里走的是"文本里带标记"的契约，不是结构化输出 —— 与 CLI 后端同因。
        """
        del schema
        self._begin_round(session_id, cancel)
        prompt = with_output_instructions(message)
        reply = self._complete(
            prompt=prompt,
            system=GENERATE_SYSTEM_PROMPT,
            timeout_ms=timeout_ms,
            on_delta=on_delta,
        )
        if not reply.strip():
            raise RuntimeError("模型没有返回任何文本（流里既没有正文也没有推理内容）")
        try:
            return parse_generated_script(reply)
        except CliContractError as error:
            raise RuntimeError(
                f"{error}\n处理：本轮会按契约失败回灌重试；若该模型始终不遵守输出格式，"
                "可在「设置 → 内置 agent」里换一个更强的模型。"
            ) from error

    def chat(
        self,
        session_id: str,
        message: str,
        timeout_ms: int,
        on_delta: Callable[[str], None] | None = None,
        cancel: Any = None,
        system_preamble: str = "",
        model: str = "",
    ) -> str:
        """自由对话：**不带输出格式要求**，返回模型的纯文本回复。

        与 `generate` 的区别与其它后端逐字一致：这里只是说话，产出的脚本不会自动执行。
        """
        self._begin_round(session_id, cancel)
        if model:
            self._model = str(model)
        return self._complete(
            prompt=message,
            system=system_preamble,
            timeout_ms=timeout_ms,
            on_delta=on_delta,
            remember=True,
        )

    # ── 内部 ──────────────────────────────────────────────────────────
    def _begin_round(self, session_id: str, cancel: Any) -> None:
        if session_id and session_id != self._session_id:
            self._session_id = str(session_id)
        self._cancel.clear()
        if cancel is not None:
            # 外部的取消原语（引擎传进来的）与自己的取消事件**或**起来用
            self._external_cancel = cancel
        else:
            self._external_cancel = None

    def _complete(
        self,
        *,
        prompt: str,
        system: str,
        timeout_ms: int,
        on_delta: Callable[[str], None] | None,
        remember: bool = False,
    ) -> str:
        model = (self._model or self._default_model or "").strip()
        if not model:
            raise RuntimeError(
                "没有指定模型：请在「设置 → 内置 agent」里填写模型名"
                "（或点「检测可用模型」从列表里选一个）。"
            )
        messages = [*self._messages, {"role": "user", "content": prompt}]
        client = self._ensure_client()
        handler = delta_stream(None, on_delta)
        try:
            completion = client.stream_chat(
                model=model,
                messages=messages,
                system=system,
                cancel=self._cancel_or_external,
                on_event=handler,
            )
        except ApiError as error:
            raise RuntimeError(str(error)) from error
        if remember and completion.content.strip():
            self._messages.append({"role": "user", "content": prompt})
            self._messages.append({"role": "assistant", "content": completion.content})
        return completion.content

    @property
    def _cancel_or_external(self) -> Any:
        external = getattr(self, "_external_cancel", None)
        if external is None:
            return self._cancel
        return _Either(self._cancel, external)

    def _ensure_client(self) -> ModelApiClient:
        with self._lock:
            if self._client is None:
                self._client = self._client_factory(
                    base_url=self._base_url, api_key=self._api_key, style=self._style
                )
            return self._client

    def _load_history(self) -> list[dict[str, str]]:
        """从运行目录读回对话历史（读不到就当新会话，不报错）。"""
        if not self._run_dir:
            return []
        try:
            from ..run_store.sessions import read_chat
        except ImportError:  # pragma: no cover - 正常情况下一定在
            return []
        try:
            entries = read_chat(self._run_dir)
        except Exception:  # noqa: BLE001 - 历史读不出来不该让对话起不来
            return []
        messages: list[dict[str, str]] = []
        for entry in entries:
            role = "assistant" if entry.role in ("model", "assistant") else "user"
            text = str(getattr(entry, "text", "") or "")
            if text.strip():
                messages.append({"role": role, "content": text})
        return messages


class _Either:
    """两个取消原语取"或"：自己置位的（abort/dispose）与外部传来的都要认。"""

    def __init__(self, first: Any, second: Any) -> None:
        self._first = first
        self._second = second

    def is_set(self) -> bool:
        for candidate in (self._first, self._second):
            if candidate is None:
                continue
            checker = getattr(candidate, "is_set", None)
            if callable(checker) and checker():
                return True
            if callable(candidate) and not hasattr(candidate, "is_set"):
                try:
                    if candidate():
                        return True
                except Exception:  # noqa: BLE001
                    pass
        return False


def make_adapter(
    *,
    base_url: str = "",
    api_key: str = "",
    style: str = "openai",
    model: str = "",
    note: Callable[[str], None] | None = None,
) -> BuiltinAdapter:
    """工厂：注册表用它造适配器（与其它后端同一个签名风格）。"""
    return BuiltinAdapter(
        base_url=base_url, api_key=api_key, style=style, model=model, note=note
    )
