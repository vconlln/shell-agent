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

from dataclasses import dataclass

from ...types import DetectedTool
from ..api_client import ApiError, ModelApiClient
from ..builtin_agent import BuiltinAdapter
from ..cli_agent import ProbeResult
from ..descriptor import BackendDescriptor

STYLE_OPENAI = "openai"
STYLE_ANTHROPIC = "anthropic"

BASE_HINT = "例如 https://api.deepseek.com/v1（OpenAI 兼容）或 https://api.deepseek.com/anthropic（Anthropic 兼容）"
KEY_HINT = "留空则读环境变量 DEEPSEEK_API_KEY / OPENAI_API_KEY / ANTHROPIC_API_KEY"

STYLE_HINT = (
    "怎么选：地址以 /anthropic 结尾（如 https://api.deepseek.com/anthropic）选「Anthropic 兼容」；"
    "以 /v1 结尾或指向 OpenAI / 本地服务（Ollama、vLLM、LM Studio）选「OpenAI 兼容」。"
)


def suggested_style(base_url: str, current: str = "") -> str:
    """按地址猜接口风格；猜不出来返回空串（**不改**用户已经选好的值）。

    判据很朴素但够用：路径里出现 `/anthropic` 就是 Anthropic 兼容端点
    （DeepSeek、智谱等都提供这种"Anthropic 兼容"入口，方便直接用 Claude 的 SDK）。
    """
    text = (base_url or "").strip().lower().rstrip("/")
    if not text:
        return ""
    if "/anthropic" in text:
        return STYLE_ANTHROPIC
    if text.endswith("/v1") or "/v1/" in text or "openai" in text:
        return STYLE_OPENAI
    return ""

@dataclass(frozen=True, slots=True)
class ProviderPreset:
    """一个服务商预设：选它就填好地址、接口风格，并给出**模型候选**。

    为什么要预设：地址与风格的组合最容易填错（用户实测把 Anthropic 兼容的地址配上
    OpenAI 风格，请求打到不存在的路径上）。而且**候选比"现取列表"更可靠** ——
    取列表是一次网络请求，可能被代理、权限、端点差异挡住；候选是本地常量，永远在。
    """

    id: str
    label: str
    base_url: str
    style: str
    models: tuple[str, ...] = ()


# 常见服务商预设。模型的写法按各自官方文档的示例（DeepSeek 的示例里就有 deepseek-flash）。
PROVIDERS: tuple[ProviderPreset, ...] = (
    ProviderPreset(
        id="deepseek",
        label="DeepSeek（OpenAI 兼容）",
        base_url="https://api.deepseek.com",
        style=STYLE_OPENAI,
        models=("deepseek-chat", "deepseek-reasoner", "deepseek-flash"),
    ),
    ProviderPreset(
        id="deepseek-anthropic",
        label="DeepSeek（Anthropic 兼容）",
        base_url="https://api.deepseek.com/anthropic",
        style=STYLE_ANTHROPIC,
        models=("deepseek-chat", "deepseek-reasoner", "deepseek-flash"),
    ),
    ProviderPreset(
        id="openai",
        label="OpenAI",
        base_url="https://api.openai.com/v1",
        style=STYLE_OPENAI,
        models=("gpt-4o-mini", "gpt-4o", "o4-mini"),
    ),
    ProviderPreset(
        id="anthropic",
        label="Anthropic",
        base_url="https://api.anthropic.com",
        style=STYLE_ANTHROPIC,
        models=("claude-sonnet-4-5", "claude-opus-4-1", "claude-haiku-4-5"),
    ),
    ProviderPreset(
        id="ollama",
        label="本地 Ollama",
        base_url="http://127.0.0.1:11434/v1",
        style=STYLE_OPENAI,
        models=("qwen2.5", "llama3.1"),
    ),
    ProviderPreset(id="custom", label="自定义", base_url="", style=STYLE_OPENAI),
)

# 兼容旧名字：注册表与设置页原来读的是 MODEL_SUGGESTIONS
MODEL_SUGGESTIONS: tuple[str, ...] = tuple(
    dict.fromkeys(model for preset in PROVIDERS for model in preset.models)
)


def provider_preset(provider_id: str) -> ProviderPreset | None:
    key = (provider_id or "").strip()
    return next((preset for preset in PROVIDERS if preset.id == key), None)


def suggest_provider(base_url: str) -> str:
    """按地址反查服务商预设（用于把已填的地址对上预设；猜不出来返回空串）。"""
    text = (base_url or "").strip().lower().rstrip("/")
    if not text:
        return ""
    if "api.deepseek.com/anthropic" in text:
        return "deepseek-anthropic"
    if "api.deepseek.com" in text:
        return "deepseek"
    if "api.openai.com" in text:
        return "openai"
    if "api.anthropic.com" in text:
        return "anthropic"
    if "11434" in text or "ollama" in text:
        return "ollama"
    return ""


def preset_models(provider_id: str, base_url: str = "") -> list[str]:
    """该服务商的模型候选（预设里写死的那几个）——取不到真实列表时的兜底。"""
    preset = provider_preset(provider_id) or provider_preset(suggest_provider(base_url))
    return list(preset.models) if preset else []

MODEL_HINT = (
    "填写模型名（会原样发给模型 API）。点「检测可用模型」可以直接从该服务商拉取真实列表；"
    "留空则无法运行 —— 这一路没有默认模型可退。"
)


def _client(config: dict[str, Any], **kwargs: Any) -> ModelApiClient:
    """按配置造客户端（base/key/风格都从设置来）。

    `ApiError` 直接往上抛（调用方 probe/list_models 会把它变成给用户看的一句话）——
    它也可能是"环境里配了 SOCKS 代理但缺 socksio"这类**构造期**错误，不能漏掉。
    """
    return ModelApiClient(
        base_url=str(config.get("base_url") or ""),
        api_key=str(config.get("api_key") or ""),
        style=str(config.get("style") or STYLE_OPENAI),
        use_proxy=bool(config.get("use_proxy", True)),
        **kwargs,
    )


def make_adapter(config: dict[str, Any]) -> BuiltinAdapter:
    """造适配器：把设置里的 base/key/风格/模型/技能交给它（没有"命令"这回事）。"""
    return BuiltinAdapter(
        base_url=str(config.get("base_url") or ""),
        api_key=str(config.get("api_key") or ""),
        style=str(config.get("style") or STYLE_OPENAI),
        model=str(config.get("model") or ""),
        note=config.get("note"),
        skills_dir=str(config.get("skills_dir") or ""),
        enabled_skills=str(config.get("enabled_skills") or ""),
        thinking=bool(config.get("thinking", False)),
        use_proxy=bool(config.get("use_proxy", True)),
        tools=bool(config.get("tools", True)),
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
    """列出可用模型（真实列表，不是候选常量）。

    超时短（20s）：这只是问一句"能不能用"，不该让界面停在"正在获取"上很久。
    """
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
    "PROVIDERS",
    "ProviderPreset",
    "preset_models",
    "provider_preset",
    "suggest_provider",
    "STYLE_HINT",
    "suggested_style",
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
