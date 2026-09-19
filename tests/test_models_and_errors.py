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
