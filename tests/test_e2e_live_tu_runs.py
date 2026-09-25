"""真实全链路冒烟：自己写一份方案 → 放进左栏 → 点「开始」→ 生成 → shellcheck → 执行 → 校验。

**默认跳过，`TU_LIVE=1` 才跑**（与 `test_e2e_live.py` 同一约定）：它要用真实模型（网络 +
API key）、真实 bash 与真实 shellcheck，还要在**真实的 tu-runs** 里留下一次运行记录。

与既有用例的分工：

| 文件 | 覆盖 |
| --- | --- |
| `test_e2e_offline.py` | 引擎全链路，但 opencode 是假的（不花钱、可反复跑） |
| `test_e2e_live.py` | 真实 opencode，只到"生成"这一步（不起执行） |
| **本文件** | 真实**内置 agent（直连模型 API）** + 真实执行，而且走的是**界面按钮那条路** |

怎么跑（在仓库根目录）：

    TU_LIVE=1 .venv/bin/python -m pytest -o addopts="" -q tests/test_e2e_live_tu_runs.py -s

运行产物（方案文档 + 运行目录）都落在用户设置里的 `run_root`（本机就是 `~/tu-runs`），
跑完可以在应用的「控制台 → 历史运行」里打开这一次，逐轮回放。

**人为闸门怎么办**：控制器有 `auto_confirm` 这个口子（它本来就是给"连跑多次/测试"用的，
见 `RunController.__init__` 的说明）。用例用它把确认答成"执行" —— 这是**测试替身**，
生产界面仍然是 `auto_confirm=False`（每次执行都弹确认框）。用例不碰那道闸门本身。
"""

from __future__ import annotations

import json
import os
import shutil
from pathlib import Path

import pytest
from PySide6.QtWidgets import QLabel, QWidget

pytestmark = pytest.mark.skipif(
    os.environ.get("TU_LIVE") != "1",
    reason="设 TU_LIVE=1 才跑真实模型的全链路冒烟（要网络 + API key）",
)

# ── 自己写的方案 ──────────────────────────────────────────────────────
# 设计要求：**可确定性验收**。所以让脚本自己造样本（不依赖目录里现成有什么），
# 并且把"该数几个文件"写死成一个能被算错的结果（c.txt 不是 .log，混进去合计就变成 6）——
# 模型漏掉过滤条件时用例会红，而不是"看起来跑通了"。
PLAN_TEXT = """\
# 运行目录体检报告

在**当前目录**（也就是本次运行目录）里做下面这些事：

1. 建一个子目录 `samples/`，在里面放三个文件：
   - `a.log`：三行内容，每行一句 `alpha-1` / `alpha-2` / `alpha-3`
   - `b.log`：两行内容，每行一句 `beta-1` / `beta-2`
   - `c.txt`：一行内容 `gamma-1`（注意：它**不是** `.log` 文件）
2. 统计 `samples/*.log` 里每个文件的**行数**，按文件名字母序，一行一个，格式固定为
   `文件名 行数 行`（例如 `a.log 3 行`）。
3. 收尾再输出一行汇总，格式固定为 `合计 N 行，共 M 个 .log 文件`（N 是行数总和，M 是
   `.log` 文件的个数；`c.txt` 不能算进去）。
4. 上面这些内容**同时**写进 `report.md`，也打印到标准输出。
5. 脚本开头必须是 `#!/usr/bin/env bash` 与 `set -euo pipefail`；
   只允许在 `samples/` 与 `report.md` 这两个地方写文件，别动其它任何东西。
"""

# 方案要求的那几行（验收就看它们）
EXPECTED_LINES = ("a.log 3 行", "b.log 2 行", "合计 5 行，共 2 个 .log 文件")


def _real_settings(tmp_path, run_root_override: str = ""):
    """读用户**真实**的设置（后端、API 地址、key、模型、run_root），但落到临时副本上。

    为什么要复制一份：`AppSettings` 会记住自己的文件，`save()` 写回的就是那个文件 ——
    用例不该改写用户的设置。复制文件再把 `loaded_from` 指向副本，就既用了真实配置
    （这正是"整体测试"的意义），又只往临时目录写字。
    """
    from tu_shell_agent.ui.settings import AppSettings, default_settings_path

    source = default_settings_path()
    if not source.is_file():
        pytest.skip(f"没有找到设置文件：{source}")
    copy = tmp_path / "settings.json"
    shutil.copyfile(source, copy)
    settings = AppSettings.load(copy)
    if run_root_override:
        settings.run_root = run_root_override
    if settings.agent_backend != "builtin":
        pytest.skip(
            f"当前后端是 {settings.agent_backend!r}，这条冒烟只覆盖内置 agent（直连模型 API）"
        )
    if not (settings.api_key or os.environ.get("DEEPSEEK_API_KEY")):
        pytest.skip("没有 API key：设置里没填、环境变量里也没有")
    if not settings.opencode_model:
        pytest.skip("没有填模型名")
    # 轮数：用户保存的是 1（引擎默认是 3）。这条要覆盖**失败回灌重试**那条路
    #（模型偶尔会把契约标记写缺、或先答一半），1 轮的话任何一次抖动都会让冒烟变红 ——
    # 那不是产品坏了，是"不许重试"。所以这里用引擎默认值，并把用户的设置打印出来。
    if settings.max_rounds < 3:
        print(f"\n[冒烟] 你的设置是 max_rounds={settings.max_rounds}（引擎默认 3）："
              "本次用 3 轮，以便覆盖失败回灌重试；建议你在设置里也改成 3。")
        settings.max_rounds = 3
    return settings


def _build_window(qtbot, settings):
    """按生产装配方式建窗口 + 控制器（唯一差别是 `auto_confirm`，见模块说明）。"""
    from tu_shell_agent.ui.main_window import MainWindow
    from tu_shell_agent.ui.run_controller import RunController

    window = MainWindow(wire_controller=False, settings=settings)
    qtbot.addWidget(window)
    window.resize(1440, 900)
    window.show()
    window.apply_appearance()
    # 不注入 opencode / toolchain：让控制器**真的**去探测并造出内置 agent（这条就是要测它）
    controller = RunController(
        window=window,
        settings=settings,
        run_root=settings.run_root,
        auto_confirm=True,
        confirm_answer=True,
    )
    return window, controller


def _write_plan(run_root: str) -> Path:
    """把方案写进 `run_root/plans/`：留一份可复用的方案文档（历史列表只认带 meta.json 的
    目录，所以 plans/ 不会污染「历史运行」）。"""
    plans = Path(run_root) / "plans"
    plans.mkdir(parents=True, exist_ok=True)
    path = plans / "运行目录体检报告.md"
    path.write_text(PLAN_TEXT, encoding="utf-8")
    return path


def test_live_plan_runs_end_to_end_in_tu_runs(qtbot, tmp_path, shellcheck_path, bash_path):
    """整条路走一遍：左栏放方案 → 点「开始」→ 生成 → shellcheck → 确认 → 执行 → 落盘。"""
    from tu_shell_agent.orchestrator.loop import LoopResult

    settings = _real_settings(tmp_path)
    assert Path(shellcheck_path).is_file() and Path(bash_path).is_file(), "执行所需的 bash/shellcheck 不在"
    plan_path = _write_plan(settings.run_root)
    window, controller = _build_window(qtbot, settings)

    window.left_pane.set_plan(str(plan_path))
    assert "运行目录体检报告" in window.left_pane.plan_text(), "左栏没有读到方案"

    results: list[LoopResult] = []
    controller.finished.connect(results.append)
    failures: list[str] = []
    controller.failed.connect(failures.append)

    window.start_button.click()                      # 与用户点的完全是同一个按钮
    qtbot.waitUntil(
        lambda: bool(results or failures) and not controller._busy(),
        timeout=420_000,                             # 生成是网络请求，给足时间
    )
    assert not failures, f"引擎异常：{failures[0]}"
    result = results[0]
    assert result.outcome == "succeeded", f"结论不是 succeeded：{result.outcome}（{result.rounds} 轮）"

    run_dir = Path(controller._run_dir)
    assert run_dir.parent == Path(settings.run_root), f"运行目录没落在 tu-runs：{run_dir}"
    print(f"\n[冒烟] 运行目录：{run_dir}（{result.rounds} 轮）")

    # ── 生成的脚本：契约解析 + shellcheck 干净 ───────────────────────
    attempt = run_dir / "attempts" / str(result.rounds)
    script = (attempt / "script.sh").read_text(encoding="utf-8")
    assert script.startswith("#!/usr/bin/env bash"), script[:60]
    assert "set -euo pipefail" in script, "方案要求了严格模式"
    findings = (attempt / "shellcheck.txt").read_text(encoding="utf-8").strip()
    assert findings == "", f"shellcheck 有发现（应当零告警）：\n{findings}"

    # ── 执行结果 ────────────────────────────────────────────────────
    stdout = (attempt / "stdout.txt").read_text(encoding="utf-8")
    for line in EXPECTED_LINES:
        assert line in stdout, f"标准输出里没有 `{line}`：\n{stdout}"

    # ── 脚本真的干了方案要求的事（在运行目录里留了东西）──────────────
    samples = run_dir / "samples"
    assert (samples / "a.log").is_file() and (samples / "b.log").is_file()
    assert (samples / "c.txt").is_file(), "方案要求建一个 .txt，用来验证过滤条件"
    assert (samples / "a.log").read_text(encoding="utf-8").count("\n") >= 3
    report = (run_dir / "report.md").read_text(encoding="utf-8")
    for line in EXPECTED_LINES:
        assert line in report, f"report.md 里没有 `{line}`：\n{report}"

    # ── 运行目录自包含：方案、配置快照、结论都在（可在「控制台 → 历史运行」里回放）──
    assert (run_dir / "plan.md").read_text(encoding="utf-8") == PLAN_TEXT
    meta = json.loads((run_dir / "meta.json").read_text(encoding="utf-8"))
    assert int(meta.get("rounds") or 0) == result.rounds
    # 用**真正的扫描器**验证"这次运行能被历史列表找到"（列表控件刷新是异步的，
    # 拿控件里的行做断言会变成在测时序）
    from tu_shell_agent.run_store.sessions import scan_sessions

    found = {Path(ref.run_dir).name for ref in scan_sessions(settings.run_root)}
    assert run_dir.name in found, f"历史扫描器找不到这次运行：{run_dir.name}（扫到 {sorted(found)[:5]}）"

    # ── 界面上看得见的东西（三区都要摆上）────────────────────────────
    assert script.splitlines()[0] in window.center_pane.current_text(), "中栏没有摆上脚本"
    # 右栏：标准输出在 output_view 里，退出码在它自己的摘要行上（两处分开断言，
    # 别把摘要行当成输出视图 —— 第一版就是这么写错的）
    assert "合计 5 行，共 2 个 .log 文件" in window.right_pane.output_view.toPlainText()
    assert "退出码 0" in window.right_pane.execute_summary.text(), (
        window.right_pane.execute_summary.text()
    )
    assert "结论：succeeded" in window.status_label.text(), window.status_label.text()


def test_live_chat_answers_into_the_new_turn_view(qtbot, tmp_path):
    """对话那条路也走一遍真模型，并且落进**新的卡片式记录区**（一轮一块 + 代码块分块）。

    这条守的是界面改动：模型答完必须变成一张卡片（用户块 + 回复块 + 页脚），
    而不是旧版那种拼在一起的纯文本。
    """
    settings = _real_settings(tmp_path)
    window, controller = _build_window(qtbot, settings)
    chat = window.chat_panel

    # 先点「新对话」：真实 run_root 里可能留着上次的对话，面板会自动接上并回填历史
    #（这是产品行为，不是缺陷）。这条用例要验的是"我这一句变成了一张卡片"，
    # 所以先把面板清空、并让下一条消息开一段新对话。
    chat.clear_history()
    controller._on_new_session()
    assert chat.transcript.findChildren(QWidget, "chatTurn") == [], "清空之后不该还有卡片"

    chat.set_backend("builtin")
    chat.input.setPlainText("只回这四个字：可以开始")
    sent: list[str] = []
    chat.send_requested.connect(sent.append)
    chat.send_button.click()
    assert sent, "点发送没有把消息发出去"

    qtbot.waitUntil(
        lambda: controller._chat_worker is None or not controller._chat_worker.isRunning(),
        timeout=240_000,
    )
    qtbot.wait(200)      # 让 done 槽（end_stream / set_busy）跑完

    turns = chat.transcript.findChildren(QWidget, "chatTurn")
    assert len(turns) == 1, f"对话应当是一轮一张卡片，实际 {len(turns)} 张"

    turn = turns[0]
    user = turn.findChild(QLabel, "chatUserText")
    assert user is not None and "可以开始" in user.text(), "用户块里没有我发的那句话"
    reply = turn.findChild(QLabel, "chatReplyText")
    assert reply is not None and reply.text().strip(), "回复块是空的（模型没有回答）"
    assert turn.findChild(QWidget, "chatTurnFooter") is not None, "卡片页脚（时间/复制）没有出现"
    print(f"\n[冒烟] 对话回复：{reply.text().strip()[:80]}")

    assert controller._run_dir, "对话没有落到运行目录里（无法在历史里找回）"
    chat_log = Path(controller._run_dir) / "chat.jsonl"
    assert chat_log.is_file(), f"对话记录没有落盘：{chat_log}"
    assert "可以开始" in chat_log.read_text(encoding="utf-8")
    # 记录里能抠出脚本块那条路仍然可用（本轮回复没有代码块 → 应当明确说"没找到"）
    from tu_shell_agent.ui.chat import extract_last_script

    assert extract_last_script(chat.transcript_text()) is None
