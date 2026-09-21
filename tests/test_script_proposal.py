"""模型给出脚本时的"提议"流程：中栏红绿 diff + 接受/拒绝（像 Cursor 的 Accept / Reject）。

用户要求："如果你能在模型对话之后，实时显示修改的代码，用绿色红色等等标出来，
并且询问用户是否接受这个代码，像 cursor 的 agent 那样"。

分工：差异正文显示在**中栏**（那里有地方、且已有一套 diff 渲染与配色），对话面板只放
摘要与两个按钮 —— 工具区本来就矮，不该再塞一个大 diff 框。
"""

from __future__ import annotations

import threading
from pathlib import Path

from tu_shell_agent.ui.chat import ChatPanel
from tu_shell_agent.ui.main_window import MainWindow
from tu_shell_agent.ui.run_controller import RunController
from tu_shell_agent.ui.settings import AppSettings
from tu_shell_agent.types import DetectionReport

OLD_SCRIPT = "#!/usr/bin/env bash\nset -euo pipefail\necho old\n"
NEW_SCRIPT = "#!/usr/bin/env bash\nset -euo pipefail\necho new\necho extra\n"
REPLY = f"我改了脚本：\n\n```bash\n{NEW_SCRIPT}```\n\n说明：换了一行并多加一行。\n"


class _StreamingAgent:
    """支持自由对话的替身：把回复按块流式吐出来，行为与真实适配器一致。"""

    def __init__(self, reply: str = REPLY) -> None:
        self._reply = reply
        self.session = "ses_proposal"

    def start(self, run_dir, agent_name="", model=None):
        return self.session

    def resume(self, run_dir, model=None):
        pass

    def chat(self, session_id, message, timeout_ms, on_delta=None, cancel=None, system_preamble="", model=""):
        for index in range(0, len(self._reply), 40):
            if on_delta is not None:
                on_delta(self._reply[index : index + 40])
        return self._reply

    def abort(self, session_id):
        pass

    def dispose(self):
        pass


class _Toolchain:
    def detect(self):
        return DetectionReport(None, None, None, ())

    def shellcheck(self, script_path):
        return [], 0, '{"comments": []}'

    def execute(self, script_path, cwd, timeout_ms, cancel=None, on_stdout=None, on_stderr=None):
        return 0, "", "", 1

    def kill_tree(self, *args, **kwargs):
        pass


def _controller(qtbot, tmp_path) -> tuple[RunController, MainWindow, _StreamingAgent]:
    settings = AppSettings(run_root=str(tmp_path / "runs"), templates_dir=str(tmp_path / "tpl"))
    window = MainWindow(wire_controller=False, settings=settings)
    qtbot.addWidget(window)
    agent = _StreamingAgent()
    controller = RunController(
        opencode=agent, toolchain=_Toolchain(), window=window, settings=settings,
        run_root=str(settings.run_root),
    )
    # 中栏先有一份"当前脚本"，否则提议会被判成"与我一样"
    window.center_pane.show_round(1, OLD_SCRIPT)
    return controller, window, agent


def _wait_for_chat(qtbot, controller) -> None:
    """等到"这一轮对话真的结束"。

    只等线程停是不够的：`worker.done` 是从线程里 emit、队列投递到主线程的，线程停了不代表
    槽函数已经跑过 —— 断言会跑在"提议还没建出来"之前（这条用例就这么假红过一次）。
    发送按钮在 `_on_chat_done` 里重新可用，是"槽函数跑过了"的可观察标志。
    """
    panel = controller.window.chat_panel
    qtbot.waitUntil(
        lambda: panel.send_button.isEnabled()
        and (controller._chat_worker is None or not controller._chat_worker.isRunning()),
        timeout=10_000,
    )


def test_reply_with_a_script_becomes_a_reviewable_proposal(qtbot, tmp_path):
    """模型回复里出现脚本 → 中栏显示红绿 diff，对话面板出现接受/拒绝。"""
    controller, window, _agent = _controller(qtbot, tmp_path)
    controller.ask("把脚本改一下")
    _wait_for_chat(qtbot, controller)

    html = window.center_pane.proposal_html()
    assert "diff-added" in html and "diff-removed" in html, "中栏没有渲染出红绿差异"
    assert window.center_pane.tabs.currentIndex() == 1, "应当自动切到差异页"

    chat = window.chat_panel
    assert chat.has_proposal(), "对话面板没有出现接受/拒绝"
    summary = chat.proposal_label.text()
    assert "新增" in summary and "删除" in summary
    # 提议**不改**中栏脚本：接受才作数
    assert window.center_pane.current_text().strip() == OLD_SCRIPT.strip()


def test_accepting_puts_the_script_in_the_center_without_running_it(qtbot, tmp_path):
    """接受 → 脚本进中栏；**不执行**（要跑得点「改后重跑」，人工闸门不绕过）。"""
    controller, window, _agent = _controller(qtbot, tmp_path)
    controller.ask("把脚本改一下")
    _wait_for_chat(qtbot, controller)

    window.chat_panel.accept_button.click()

    assert window.center_pane.current_text().strip() == NEW_SCRIPT.strip()
    assert not window.chat_panel.has_proposal(), "接受后提议栏应当消失"
    assert window.center_pane.proposal_html() == ""
    assert window.center_pane.tabs.currentIndex() == 0, "接受后回到「本轮」"
    assert "改后重跑" in window.chat_panel.status.text(), "要告诉用户下一步怎么跑"


def test_rejecting_keeps_the_current_script(qtbot, tmp_path):
    """拒绝 → 中栏脚本一字不改，只在记录里留一句。"""
    controller, window, _agent = _controller(qtbot, tmp_path)
    controller.ask("把脚本改一下")
    _wait_for_chat(qtbot, controller)

    window.chat_panel.reject_button.click()

    assert window.center_pane.current_text().strip() == OLD_SCRIPT.strip()
    assert not window.chat_panel.has_proposal()
    assert "已拒绝" in window.chat_panel.transcript.toPlainText()


def test_a_reply_without_a_script_makes_no_proposal(qtbot, tmp_path):
    """纯聊天（没有脚本块）不该弹出接受/拒绝 —— 那会让人以为模型改了代码。"""
    controller, window, _agent = _controller(qtbot, tmp_path)
    _agent._reply = "这条报告的意思是：shellcheck 认为变量没加引号。"
    controller.ask("这条报告什么意思")
    _wait_for_chat(qtbot, controller)

    assert not window.chat_panel.has_proposal()
    assert window.center_pane.proposal_html() == ""


def test_accept_and_run_goes_through_the_verify_path(qtbot, tmp_path):
    """「接受并重跑」= 接受 + 改后重跑，走的是同一条路（闸门一个都不少）。

    用户要的是 Cursor 那种"接着就能跑"的流程；这里快捷的是点击次数，不是安全边界 ——
    所以断言它调用的**就是** `verify_edited`（那里面有 shellcheck 与人工确认）。
    """
    controller, window, _agent = _controller(qtbot, tmp_path)
    controller.ask("把脚本改一下")
    _wait_for_chat(qtbot, controller)

    calls: list[str] = []
    real_verify = controller.verify_edited
    controller.verify_edited = lambda: (calls.append("verify"), real_verify())[1]

    window.chat_panel.accept_run_button.click()

    assert calls == ["verify"], "「接受并重跑」没有走到校验与执行那条路"
    assert window.center_pane.current_text().strip() == NEW_SCRIPT.strip(), "脚本没进中栏"
    assert not window.chat_panel.has_proposal()
    assert "校验并执行" in window.chat_panel.status.text()


def test_accept_and_run_without_a_proposal_is_a_no_op(qtbot, tmp_path):
    """没有待决定的提议时点它不能出事（按钮可能还留在界面上）。"""
    controller, window, _agent = _controller(qtbot, tmp_path)
    calls: list[str] = []
    controller.verify_edited = lambda: calls.append("verify")

    window.chat_panel.accept_run_button.click()

    assert calls == []
    assert window.center_pane.current_text().strip() == OLD_SCRIPT.strip()
