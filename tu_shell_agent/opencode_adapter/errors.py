"""把 opencode / provider 的报错翻译成"该怎么办"。

只做**已知且可操作**的映射：没有对应项时返回空串，界面就不会多废话。
"""

from __future__ import annotations

# 免费档只能在官方客户端里用 —— 本应用经 serve 的 HTTP API 调用，必然被拒。
_FREE_TIER_MARKERS = (
    "free tier can only be used from within opencode",
    "free tier",
)
_MISSING_MODEL_HINTS = (
    "provider not found",
    "model not found",
    "no model",
)


def explain_provider_error(text: str) -> str:
    """返回一句可操作的建议；认不出来就返回空串（不猜、不啰嗦）。"""
    lowered = (text or "").lower()
    if any(marker in lowered for marker in _FREE_TIER_MARKERS):
        return (
            "原因：opencode 当前没有可用的默认模型，落到了它的免费档，"
            "而免费档只能在官方客户端内使用。\n"
            "处理：在「设置 → 模型」中检测并选择一个已配置凭据的模型"
            "（例：deepseek/deepseek-v4-pro），然后重新发起。"
        )
    if any(marker in lowered for marker in _MISSING_MODEL_HINTS):
        return "处理：在「设置 → 模型」中重新检测并选择一个可用模型。"
    return ""
