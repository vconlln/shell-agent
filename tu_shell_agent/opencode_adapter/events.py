"""SSE 解析（规格 §3：事件流是 SSE，信封 {type, properties}）。

自己解析而不依赖 SDK：Python 没有官方 SDK，而 SSE 是文档化接口，行为可预期。
"""

from __future__ import annotations

import json
from typing import Any

Event = dict[str, Any]


class SseParser:
    """一帧可能拆成多个 `data:` 行，也可能拆到多个网络分片里。

    策略：累积 data 行，直到拼出的字符串能被 json 解析为止；
    遇到空行（事件边界）仍未解析成功则丢弃该坏帧。
    """

    def __init__(self) -> None:
        self._buffer = ""
        self._pending = ""

    def push(self, chunk: str) -> list[Event]:
        self._buffer += chunk
        events: list[Event] = []
        while True:
            index = self._buffer.find("\n")
            if index == -1:
                break
            line = self._buffer[:index].rstrip("\r")
            self._buffer = self._buffer[index + 1 :]

            if line.startswith("data:"):
                piece = line[len("data:") :].lstrip()
                self._pending = piece if not self._pending else f"{self._pending}\n{piece}"
                try:
                    events.append(json.loads(self._pending))
                    self._pending = ""
                except json.JSONDecodeError:
                    pass
            elif line == "" and self._pending:
                self._pending = ""
            # 注释行（以 : 开头）与其它字段直接忽略
        return events


def event_text(event: Event) -> str | None:
    part = (event.get("properties") or {}).get("part")
    if isinstance(part, dict) and part.get("type") == "text" and isinstance(part.get("text"), str):
        return part["text"]
    return None


def delta_text(event: Event) -> str | None:
    """message.part.delta 的增量文本（1.18.31 的流式通道）。

    properties = {sessionID, messageID, partID, field, delta}；只认 field == "text"。
    与 message.part.updated 的区别：后者带的是**整个** part（累计文本），混用会重复。
    """
    if event.get("type") != "message.part.delta":
        return None
    properties = event.get("properties") or {}
    if properties.get("field") != "text":
        return None
    delta = properties.get("delta")
    return delta if isinstance(delta, str) else None


# 1.18.31 的真实事件名是 permission.asked（EventPermissionAsked）；
# permission.updated 是旧文档里的写法，一并认下来以防版本差异。
_PERMISSION_EVENTS = ("permission.asked", "permission.updated")


def is_permission_ask(event: Event) -> tuple[str, str] | None:
    """返回 (session_id, permission_id)，不是权限询问则 None。"""
    if event.get("type") not in _PERMISSION_EVENTS:
        return None
    properties = event.get("properties") or {}
    permission_id = properties.get("id")
    session_id = properties.get("sessionID")
    if isinstance(permission_id, str) and isinstance(session_id, str):
        return session_id, permission_id
    return None
