"""shellcheck 封装（规格 §11）：固定参数 + 退出码语义。"""

from __future__ import annotations

import json
import os
import subprocess
from typing import Mapping

from ..procflags import no_window_kwargs
from ..types import ShellcheckFinding

LEVELS = ("error", "warning", "info", "style")


class ShellcheckError(RuntimeError):
    """shellcheck 自身出问题（文件读不了 / 参数错 / 未知 formatter），不是脚本有问题。"""

    def __init__(self, message: str, exit_code: int | None, command: str, stderr: str) -> None:
        super().__init__(message)
        self.exit_code = exit_code
        self.command = command
        self.stderr = stderr


def parse_json1(raw: str) -> list[ShellcheckFinding]:
    payload = json.loads(raw or "{}")
    findings: list[ShellcheckFinding] = []
    for comment in payload.get("comments", []):
        level = comment.get("level", "warning")
        findings.append(
            ShellcheckFinding(
                code=f"SC{comment['code']}",
                line=int(comment["line"]),
                column=int(comment["column"]),
                level=level if level in LEVELS else "warning",  # type: ignore[arg-type]
                message=comment.get("message", ""),
            )
        )
    return findings


def build_env(env: Mapping[str, str]) -> dict[str, str]:
    """必须删除 SHELLCHECK_OPTS：它会被隐式前置，污染结果。"""
    copy = dict(env)
    copy.pop("SHELLCHECK_OPTS", None)
    return copy


def run_shellcheck(
    shellcheck_path: str,
    script_path: str,
    extra_args: list[str] | None = None,
) -> tuple[list[ShellcheckFinding], int, str]:
    """返回 (发现列表, 退出码, 原始 json1 文本)。

    固定参数：--norc（不读用户 .shellcheckrc）、-s bash（Git Bash 即 bash）、-f json1。
    退出码语义：0 干净 / 1 有问题 / 2 文件无法处理 / 3、4 调用错误。
    """
    args = ["--norc", "-s", "bash", "-f", "json1", *(extra_args or []), "--", script_path]
    command = " ".join([shellcheck_path, *args])

    completed = subprocess.run(
        [shellcheck_path, *args],
        capture_output=True,
        text=True,
        env=build_env(os.environ),
        **no_window_kwargs(),
    )
    exit_code = completed.returncode

    if exit_code in (0, 1):
        return parse_json1(completed.stdout), exit_code, completed.stdout

    hints = {2: "文件无法处理", 3: "参数语法错误", 4: "未知 formatter/选项"}
    hint = hints.get(exit_code, "shellcheck 异常退出")
    raise ShellcheckError(
        f"shellcheck 调用失败（退出码 {exit_code}，{hint}）：{command}",
        exit_code,
        command,
        completed.stderr,
    )
