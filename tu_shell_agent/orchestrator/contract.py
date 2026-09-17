"""产出契约校验（规格 §7.3、§11）。"""

from __future__ import annotations

import re

from ..types import ContractResult

MAX_SCRIPT_BYTES = 64 * 1024

_ANCHOR = re.compile(r"#\s*(@@TU:[A-Z0-9_]+@@)")
_FENCE = re.compile(r"^```[a-zA-Z]*\n([\s\S]*?)\n?```$")


def extract_anchors(text: str) -> tuple[str, ...]:
    return tuple(_ANCHOR.findall(text))


def normalize_script(raw: str) -> str:
    """去 markdown 围栏、去首尾空白、归一化 LF、结尾恰好一个换行。"""
    text = raw.replace("\r\n", "\n").replace("\r", "\n").strip()
    fenced = _FENCE.match(text)
    if fenced:
        text = fenced.group(1).strip()
    return text + "\n"


def check_contract(normalized: str, required_anchors: tuple[str, ...]) -> ContractResult:
    size = len(normalized.encode("utf-8"))
    missing = tuple(anchor for anchor in required_anchors if anchor not in normalized)

    if not normalized.strip():
        return ContractResult(False, "empty", missing, size)
    if size > MAX_SCRIPT_BYTES:
        return ContractResult(False, "too_large", missing, size)
    if "\r" in normalized:
        return ContractResult(False, "has_crlf", missing, size)
    if missing:
        return ContractResult(False, "missing_anchor", missing, size)
    return ContractResult(True, None, (), size)
