"""内置 agent 后端条目：**直连模型 API**，不经过任何别人的 CLI。

用户要求："我要做的是自己的 agent，你也可以自己做 agent 不用调用别的后端，当然这是最好"。

与其它后端的差别只有"谁来生成文本"：

| | opencode | claude / codeagent | **内置（本文件）** |
| --- | --- | --- | --- |
| 谁来生成 | opencode serve 的 HTTP API | 各自的 CLI，每轮起一个进程 | **直接 POST 模型 API，SSE 流式** |
| 首字延迟 | 起 serve + 建会话 | 起 node 进程（几秒冷启动） | 只有模型自己的首 token |
| 思考过程 | 由 CLI 决定 | 由 CLI 决定 | `reasoning_content` / `thinking_delta` **逐字显示** |
| 工具能力 | 由 agent 定义文件的 permission 收敛 | 由 `--disallowedTools` 收敛 | **没有工具**（比前两者更窄） |

安全模型一字不变：它只产生文本，脚本由引擎按契约解析、写盘、shellcheck、人工确认后才执行。
"""

from __future__ import annotations

from typing import Any

from ...types import DetectedTool
from ..builtin_agent import BuiltinAdapter
from ..cli_agent import ProbeResult
from ..descriptor import BackendDescriptor
from ..api_client import ApiError, ModelApiClient

STYLE_OPENAI = "openai"
STYLE_ANTHROPIC = "anthropic"

BASE_HINT = "例如 https://api.deepseek.com/v1（OpenAI 兼容）或 https://api.anthropic.com"
KEY_HINT = "留空则读环境变量 DEEPSEEK_API_KEY / OPENAI_API_KEY / ANTHROPIC_API_KEY"

# 常见服务商给一组候选（只为让用户知道该填什么形状；真实列表用「检测可用模型」现取）。
MODEL_SUGGESTIONS: tuple[str, ...] = (
    "deepseek-chat",
    "deepseek-reasoner",
    "gpt-4o-mini",
    "claude-sonnet-4-5",
)

MODEL_HINT = (
    "填写模型名（会原样发给模型 API）。点「检测可用模型」可以直接从该服务商拉取真实列表；"
    "留空则无法运行 —— 这一路没有默认模型可退。"
)


def _client(config: dict[str, Any], **kwargs: Any) -> ModelApiClient:
    """按配置造客户端（base/key/风格/超时都从设置来）。"""
    return ModelApiClient(
        base_url=str(config.get("base_url") or ""),
        api_key=str(config.get("api_key") or ""),
        style=str(config.get("style") or STYLE_OPENAI),
        **kwargs,
    )


def make_adapter(config: dict[str, Any]) -> BuiltinAdapter:
    """造适配器：把设置里的 base/key/风格/模型交给它（没有"命令"这回事）。"""
    return BuiltinAdapter(
        base_url=str(config.get("base_url") or ""),
        api_key=str(config.get("api_key") or ""),
        style=str(config.get("style") or STYLE_OPENAI),
        model=str(config.get("model") or ""),
        note=config.get("note"),
    )


def probe(config: dict[str, Any]) -> ProbeResult:
    """「检测」按钮：拿配置真的问一次模型 API（列模型是最便宜的"能用吗"检查）。

    为什么要真发一次请求：命令行后端的检测是"跑一次 --version"，这里对应的就是"能不能列到模型"。
    只检查"字段填没填"会把"key 写错了"报成"通过"，那比不检测更糟。
    """
    base = str(config.get("base_url") or "").strip()
    if not base:
        return ProbeResult(None, "没有填写 API 地址（" + BASE_HINT + "）")
    if not str(config.get("api_key") or "").strip():
        return ProbeResult(
            None,
            "没有填写 API key（" + KEY_HINT + "）：多数服务商不接受匿名请求。",
        )
    try:
        models = list_models(config)
    except ApiError as error:
        return ProbeResult(None, str(error))
    except Exception as error:  # noqa: BLE001 - 探测绝不许把异常抛给界面线程
        return ProbeResult(None, f"检测失败：{error}")
    version = f"{len(models)} 个模型可用" if models else "连通（未列到模型）"
    return ProbeResult(DetectedTool(path=base, version=version), "")


def list_models(config: dict[str, Any]) -> list[str]:
    """列出可用模型（真实列表，不是候选常量）。"""
    client = _client(config)
    try:
        return client.list_models()
    finally:
        client.close()


DESCRIPTOR = BackendDescriptor(
    id="builtin",
    display_name="内置 agent（直连模型 API）",
    default_command="",
    version_args=(),
    needs_serve=False,
    summary=(
        "直接调用模型 API（OpenAI 兼容或 Anthropic 兼容），流式返回，并且把推理过程一并显示。"
        "不需要安装任何命令行 agent。"
    ),
    install_hint="填写 API 地址与 key 即可（无需安装其它工具）",
    factory=lambda _command, _note=None: make_adapter({}),   # 不会被用到，见 api_factory
    api_factory=make_adapter,
    api_probe=probe,
    api_model_list=list_models,
    model_suggestions=MODEL_SUGGESTIONS,
    model_hint=MODEL_HINT,
    api_base_hint=BASE_HINT,
    api_key_hint=KEY_HINT,
)

__all__ = [
    "BASE_HINT",
    "DESCRIPTOR",
    "KEY_HINT",
    "MODEL_HINT",
    "MODEL_SUGGESTIONS",
    "STYLE_ANTHROPIC",
    "STYLE_OPENAI",
    "list_models",
    "make_adapter",
    "probe",
]
