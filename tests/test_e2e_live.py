"""真实 opencode 冒烟。默认跳过，TU_LIVE=1 才跑（需要本机已装并登录 opencode 1.x）。"""

import os
from dataclasses import dataclass, field

import pytest

from tu_shell_agent.opencode_adapter import OpencodeAdapter
from tu_shell_agent.opencode_adapter.agent_file import AGENT_NAME
from tu_shell_agent.orchestrator.prompt import OUTPUT_SCHEMA
from tu_shell_agent.shell_toolchain.detect import detect_all, system_deps

pytestmark = pytest.mark.skipif(
    os.environ.get("TU_LIVE") != "1", reason="设 TU_LIVE=1 才跑真实 opencode 冒烟"
)


def test_live_generation_returns_structured_output(tmp_path):
    report = detect_all(system_deps())
    assert report.opencode is not None, "\n".join(report.problems)

    adapter = OpencodeAdapter(
        opencode_path=report.opencode.path,
        note=lambda message: print(f"[opencode] {message}"),
    )
    try:
        session_id = adapter.start(str(tmp_path), AGENT_NAME)
        generated = adapter.generate(
            session_id,
            "在这个骨架里实现：打印当前目录下所有 .md 文件的数量。\n"
            "骨架：# @@TU:BODY@@\n运行目录：" + str(tmp_path),
            OUTPUT_SCHEMA,
            timeout_ms=180_000,
        )
        assert "@@TU:BODY@@" in generated.script
        assert generated.notes != ""
    finally:
        adapter.dispose()
