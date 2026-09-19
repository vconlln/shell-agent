"""列出 opencode 可用的模型。

为什么要专门做这件事：opencode 自己**没有**默认模型时，会落到它的免费档
（`opencode/*-free`），而免费档只允许官方客户端调用 —— 本应用是经 `opencode serve`
的 HTTP API 调用的，会拿到 "OpenCode's free tier can only be used from within OpenCode"。
所以界面上必须让用户显式选一个"自己配了凭据的模型"，这个模块负责把可选项取出来。

`ui/**` 不允许直接碰子进程（架构分层：外部世界只在 ports/适配器后面），所以这条 CLI
调用放在适配器层；界面用 QThread 包一层调用它。
"""

from __future__ import annotations

import os
import subprocess

# `opencode models` 正常很快（本地读配置 + 内置清单），给 30s 已经很宽松。
DEFAULT_TIMEOUT_S = 30.0


def parse_models(stdout: str) -> list[str]:
    """把 `opencode models` 的输出解析成 `provider/model` 列表（去掉噪音、保持稳定顺序）。"""
    models: list[str] = []
    for line in stdout.splitlines():
        text = line.strip()
        if not text or "/" not in text:
            continue
        if any(character.isspace() for character in text):
            continue                      # 带空格的不是模型 id（多半是提示信息）
        if text not in models:
            models.append(text)
    return models


def list_models(opencode_path: str = "opencode", *, timeout_s: float = DEFAULT_TIMEOUT_S) -> list[str]:
    """跑一次 `opencode models`。失败时抛 RuntimeError，消息直接可以给用户看。"""
    command = [opencode_path or "opencode", "models"]
    try:
        completed = subprocess.run(
            command,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout_s,
            check=False,
            env={**os.environ, "NO_COLOR": "1"},
        )
    except FileNotFoundError as error:
        raise RuntimeError(f"找不到 opencode：{opencode_path}") from error
    except subprocess.TimeoutExpired as error:
        raise RuntimeError(f"`opencode models` 超过 {timeout_s:.0f}s 没有返回") from error
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout or "").strip().splitlines()
        raise RuntimeError(f"`opencode models` 退出码 {completed.returncode}：{detail[-1] if detail else '无输出'}")
    return parse_models(completed.stdout)


def split_model(reference: str) -> tuple[str, str] | None:
    """`provider/model` → `(provider, model)`；格式不对时返回 None。

    opencode 的模型 id 里可能带斜杠（`provider/org/model`），所以只按**第一个**斜杠切。
    """
    text = (reference or "").strip()
    if "/" not in text:
        return None
    provider, _, model = text.partition("/")
    if not provider.strip() or not model.strip():
        return None
    return provider.strip(), model.strip()
