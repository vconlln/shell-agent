"""模型列表与 provider 报错的翻译（`opencode_adapter/models.py` / `errors.py`）。"""

from __future__ import annotations

import subprocess

import pytest

from tu_shell_agent.opencode_adapter import models as models_module
from tu_shell_agent.opencode_adapter.errors import explain_provider_error


# ── 解析 ──────────────────────────────────────────────────────────────


def test_parse_models_keeps_only_provider_model_lines():
    """`opencode models` 输出每行一个 `provider/model`；噪音行必须被丢掉。"""
    stdout = (
        "opencode/big-pickle\n"
        "\n"
        "正在读取配置...\n"
        "deepseek/deepseek-v4-pro\n"
        "  glms-3/glm-5.3  \n"
        "opencode/big-pickle\n"          # 重复的只留一次
        "没有斜杠的一行\n"
    )
    assert models_module.parse_models(stdout) == [
        "opencode/big-pickle",
        "deepseek/deepseek-v4-pro",
        "glms-3/glm-5.3",
    ]


def test_list_models_runs_the_cli(monkeypatch):
    captured = {}

    def fake_run(command, **kwargs):
        captured["command"] = command
        return subprocess.CompletedProcess(command, 0, "a/one\nb/two\n", "")

    monkeypatch.setattr(models_module.subprocess, "run", fake_run)
    assert models_module.list_models("/usr/bin/opencode") == ["a/one", "b/two"]
    assert captured["command"] == ["/usr/bin/opencode", "models"]


def test_list_models_reports_failures_readably(monkeypatch):
    """失败要说清楚是什么失败了 —— 这句话会原样出现在设置页的提示里。"""
    monkeypatch.setattr(
        models_module.subprocess,
        "run",
        lambda command, **kwargs: subprocess.CompletedProcess(command, 2, "", "boom\n"),
    )
    with pytest.raises(RuntimeError, match="退出码 2"):
        models_module.list_models("opencode")

    def missing(command, **kwargs):
        raise FileNotFoundError(command[0])

    monkeypatch.setattr(models_module.subprocess, "run", missing)
    with pytest.raises(RuntimeError, match="找不到 opencode"):
        models_module.list_models("/nope/opencode")


# ── 报错翻译 ──────────────────────────────────────────────────────────


def test_free_tier_error_becomes_an_actionable_hint():
    text = (
        "opencode 返回错误 APIError：Error from provider (Console): "
        "OpenCode's free tier can only be used from within OpenCode"
    )
    hint = explain_provider_error(text)
    assert "免费档" in hint and "设置 → 模型" in hint


def test_unknown_errors_get_no_invented_advice():
    """认不出来就什么都不说 —— 编一句错误的建议比不说更糟。"""
    assert explain_provider_error("connection reset by peer") == ""
    assert explain_provider_error("") == ""


# ── 模型引用解析与逐条指定模型 ────────────────────────────────────────


def test_split_model_handles_provider_and_nested_ids():
    """`provider/model` → (provider, model)；只按第一个斜杠切（模型 id 里可能还有斜杠）。"""
    assert models_module.split_model("deepseek/deepseek-v4-pro") == ("deepseek", "deepseek-v4-pro")
    assert models_module.split_model("opencode/org/model") == ("opencode", "org/model")
    assert models_module.split_model("  空格/ 也认  ") == ("空格", "也认")
    assert models_module.split_model("没有斜杠") is None
    assert models_module.split_model("/只有斜杠") is None
    assert models_module.split_model("") is None


def test_chat_sends_the_chosen_model_with_each_message():
    """换模型是按**每条消息**传的（opencode 的 message 接口支持 `model: {providerID, modelID}`），
    所以对话里换模型不必重启 serve、也不动 agent 文件。"""
    from tu_shell_agent.opencode_adapter import OpencodeAdapter

    captured: dict = {}

    class _Response:
        status_code = 200

        @staticmethod
        def json():
            return {"info": {"id": "msg_1"}, "parts": [{"type": "text", "text": "好的"}]}

        @staticmethod
        def raise_for_status():
            return None

    class _Client:
        @staticmethod
        def post(url, json=None, timeout=None):     # noqa: A002 - 与 httpx 同名
            captured["url"] = url
            captured["payload"] = json
            return _Response()

    adapter = OpencodeAdapter("opencode")
    adapter._client = _Client()                      # 只替换网络出口，其余走真实代码

    assert adapter.chat("ses_1", "你好", 5_000, model="glms-3/glm-5.3") == "好的"
    assert captured["payload"]["model"] == {"providerID": "glms-3", "modelID": "glm-5.3"}

    adapter.chat("ses_1", "再问", 5_000)             # 不指定 = 沿用会话自己的模型
    assert "model" not in captured["payload"]
