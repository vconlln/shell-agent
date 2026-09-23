"""后端选择的界面契约：设置字段、设置页的后端分组、自检页的显示名、控制器的装配。

这里全部用替身或临时文件，**不起真实子进程**（适配器层的进程行为由
`tests/test_agent_backends.py` 用假 CLI 覆盖）：这些用例要钉的是"界面与控制器有没有按
设置里那个后端走"，而不是"命令行怎么拼"。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from PySide6.QtCore import QThread, Signal

from tu_shell_agent.agent_backends import ProbeResult
from tu_shell_agent.types import DetectedTool, DetectionReport
from tu_shell_agent.ui.engine_worker import BackendProbeWorker
from tu_shell_agent.ui.main_window import MainWindow
from tu_shell_agent.ui.pages.selfcheck import SelfCheckPage
from tu_shell_agent.ui.pages.settings_page import SettingsPage
from tu_shell_agent.ui.run_controller import RunController
from tu_shell_agent.ui.settings import AppSettings

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "fake_agent.sh"


class _FakeOpencode:
    def start(self, run_dir, agent_name, model):
        return "ses_ctrl"

    def generate(self, session_id, message, schema, timeout_ms, on_delta=None, cancel=None):
        raise AssertionError("注入替身时不该走到这里")

    def abort(self, session_id):
        pass

    def dispose(self):
        pass


class _FakeToolchain:
    def detect(self):
        return DetectionReport(None, None, None, ())

    def shellcheck(self, script_path):
        return [], 0, '{"comments": []}'

    def execute(self, script_path, cwd, timeout_ms, cancel=None, on_stdout=None, on_stderr=None):
        raise AssertionError("注入替身时不该走到这里")


# ── 设置字段 ──────────────────────────────────────────────────────────


def test_backend_fields_round_trip_through_the_file(tmp_path):
    path = tmp_path / "settings.json"
    settings = AppSettings.load(path)
    assert settings.agent_backend == "opencode"      # 默认 = 本功能之前的行为
    assert settings.agent_command == ""

    settings.agent_backend = "claude"
    settings.agent_command = "/opt/claude"
    settings.save()

    again = AppSettings.load(path)
    assert (again.agent_backend, again.agent_command) == ("claude", "/opt/claude")


def test_unknown_backend_value_in_the_file_converges_to_the_default(tmp_path):
    """文件被手改成不认识的 id 时收敛回默认后端，而且**不许**碰 `_loaded_from`。

    两条都在这里钉住：收敛漏了会让下拉选不中（用户看不出在跑哪个后端）；
    顺手清掉 `_loaded_from` 则会让"读进来之后点保存"直接报"未指定保存路径"（踩过的坑）。
    """
    path = tmp_path / "settings.json"
    path.write_text('{"agent_backend": "未来的某个后端"}', encoding="utf-8")

    settings = AppSettings.load(path)
    assert settings.agent_backend == "opencode"
    assert settings.loaded_from == path
    settings.save()                                   # 不传路径：必须还能写
    assert json.loads(path.read_text(encoding="utf-8"))["agent_backend"] == "opencode"


def test_wrongly_typed_backend_value_falls_back_to_the_default(tmp_path):
    path = tmp_path / "settings.json"
    path.write_text('{"agent_backend": 7}', encoding="utf-8")
    assert AppSettings.load(path).agent_backend == "opencode"


# ── 设置页：后端分组 ──────────────────────────────────────────────────


def _page(qtbot, path: Path) -> SettingsPage:
    page = SettingsPage()
    qtbot.addWidget(page)
    page.set_settings(AppSettings.load(path))
    return page


def test_settings_page_lists_every_registered_backend(qtbot, tmp_path):
    page = _page(qtbot, tmp_path / "settings.json")
    listed = {
        page.backend_combo.itemData(index): page.backend_combo.itemText(index)
        for index in range(page.backend_combo.count())
    }
    assert listed == {
        "opencode": "opencode",
        "claude": "Claude Code",
        "codeagent": "codeagent",
        "custom": "自定义命令行",
    }
    assert page.backend_combo.currentData() == "opencode"


def test_backend_choice_and_command_are_saved(qtbot, tmp_path):
    path = tmp_path / "settings.json"
    page = _page(qtbot, path)

    page.backend_combo.setCurrentIndex(page.backend_combo.findData("codeagent"))
    page.agent_command_edit.setText("/opt/codeagent")
    page.save_button.click()

    on_disk = json.loads(path.read_text(encoding="utf-8"))
    assert on_disk["agent_backend"] == "codeagent"
    assert on_disk["agent_command"] == "/opt/codeagent"

    reopened = _page(qtbot, path)
    assert reopened.backend_combo.currentData() == "codeagent"
    assert reopened.agent_command_edit.text() == "/opt/codeagent"


def test_settings_page_hint_says_which_command_will_run(qtbot, tmp_path):
    """提示行要如实说明当前后端与**解析出来的**命令（显示的命令必须与实际执行的一致）。"""
    page = _page(qtbot, tmp_path / "settings.json")

    page.backend_combo.setCurrentIndex(page.backend_combo.findData("claude"))
    assert "Claude Code" in page.backend_hint.text()
    assert "claude" in page.backend_hint.text()          # 留空 → 默认命令
    assert page.effective_command() == "claude"

    page.agent_command_edit.setText("/opt/my-claude")
    assert "/opt/my-claude" in page.backend_hint.text()
    assert page.effective_command() == "/opt/my-claude"


def test_settings_page_hint_admits_when_the_command_is_missing(qtbot, tmp_path):
    """自定义后端没填命令时必须说出来，而不是让用户以为已经配好了。"""
    page = _page(qtbot, tmp_path / "settings.json")
    page.backend_combo.setCurrentIndex(page.backend_combo.findData("custom"))

    assert page.effective_command() == ""
    assert "尚未填写" in page.backend_hint.text()


def test_settings_page_hint_falls_back_to_the_opencode_component_path(qtbot, tmp_path):
    path = tmp_path / "settings.json"
    path.write_text('{"opencode_path": "/opt/opencode"}', encoding="utf-8")
    page = _page(qtbot, path)

    assert page.effective_command() == "/opt/opencode"
    assert "/opt/opencode" in page.backend_hint.text()


class _StubProbeWorker(QThread):
    """替身探测 worker：把结果直接发出来，不起任何进程。"""

    done = Signal(object)
    failed = Signal(str)

    calls: list[tuple[str, str]] = []
    result = ProbeResult(DetectedTool(path="/usr/bin/claude", version="2.1.112"), "")

    def __init__(self, backend_id: str, command: str = "", parent=None) -> None:
        super().__init__(parent)
        self._backend_id = backend_id
        self._command = command
        _StubProbeWorker.calls.append((backend_id, command))

    def run(self) -> None:  # noqa: D102 - 替身
        if self.result.ok:
            self.done.emit(self.result)
        else:
            self.failed.emit(self.result.message)


@pytest.fixture
def stub_probe(monkeypatch):
    _StubProbeWorker.calls = []
    _StubProbeWorker.result = ProbeResult(
        DetectedTool(path="/usr/bin/claude", version="2.1.112"), ""
    )
    monkeypatch.setattr(
        "tu_shell_agent.ui.engine_worker.BackendProbeWorker", _StubProbeWorker
    )
    yield _StubProbeWorker
    monkeypatch.setattr(
        "tu_shell_agent.ui.engine_worker.BackendProbeWorker", BackendProbeWorker
    )


def test_detect_button_runs_the_probe_for_the_selected_backend(qtbot, tmp_path, stub_probe):
    page = _page(qtbot, tmp_path / "settings.json")
    page.backend_combo.setCurrentIndex(page.backend_combo.findData("claude"))

    page.backend_detect_button.click()
    qtbot.waitUntil(lambda: "命令可用" in page.backend_hint.text(), timeout=5_000)

    assert stub_probe.calls == [("claude", "")]          # 命令留空时由注册表补默认值
    assert "2.1.112" in page.backend_hint.text()
    assert page.backend_detect_button.isEnabled()        # 检测完按钮要恢复可点


def test_detect_button_reports_a_failure_in_the_hint(qtbot, tmp_path, stub_probe):
    stub_probe.result = ProbeResult(None, "找不到命令 claude：请安装 Claude Code")
    page = _page(qtbot, tmp_path / "settings.json")
    page.backend_combo.setCurrentIndex(page.backend_combo.findData("claude"))

    page.backend_detect_button.click()
    qtbot.waitUntil(lambda: "找不到命令" in page.backend_hint.text(), timeout=5_000)

    assert "安装" in page.backend_hint.text()            # 失败也要给出下一步
    assert page.backend_detect_button.isEnabled()


def test_switching_backend_drops_the_previous_detection_result(qtbot, tmp_path, stub_probe):
    """换了后端还留着上一次的"命令可用"，会被读成"当前这个也能用"。"""
    page = _page(qtbot, tmp_path / "settings.json")
    page.backend_combo.setCurrentIndex(page.backend_combo.findData("claude"))
    page.backend_detect_button.click()
    qtbot.waitUntil(lambda: "命令可用" in page.backend_hint.text(), timeout=5_000)

    page.backend_combo.setCurrentIndex(page.backend_combo.findData("custom"))

    assert "命令可用" not in page.backend_hint.text()
    assert "尚未填写" in page.backend_hint.text()


def test_model_hint_follows_the_backend(qtbot, tmp_path):
    """opencode 的免费档提示对着命令行后端显示就是误导，提示要跟着后端换。"""
    page = _page(qtbot, tmp_path / "settings.json")
    assert "免费档" in page.model_hint.text()

    page.backend_combo.setCurrentIndex(page.backend_combo.findData("claude"))
    assert "免费档" not in page.model_hint.text()
    assert "--model" in page.model_hint.text()


def test_model_list_detection_is_not_offered_for_cli_backends(qtbot, tmp_path, monkeypatch):
    """模型列表是 `opencode models` 的东西：命令行后端下点它只会报一条无关的失败。"""
    started: list[object] = []
    monkeypatch.setattr(
        "tu_shell_agent.ui.engine_worker.ModelsWorker", lambda *a, **k: started.append(a)
    )
    page = _page(qtbot, tmp_path / "settings.json")
    page.backend_combo.setCurrentIndex(page.backend_combo.findData("claude"))

    page.model_refresh_button.click()

    assert started == []
    assert "不提供模型列表" in page.model_hint.text()


# ── 自检页：显示当前后端 ──────────────────────────────────────────────


def _report(version: str = "2.1.112") -> DetectionReport:
    return DetectionReport(
        opencode=DetectedTool(path="/usr/bin/claude", version=version),
        bash=DetectedTool(path="/usr/bin/bash", version="5.3.15"),
        shellcheck=DetectedTool(path="/usr/bin/shellcheck", version="0.11.0"),
        problems=(),
    )


def test_selfcheck_page_shows_the_selected_backend_name(qtbot):
    page = SelfCheckPage()
    qtbot.addWidget(page)

    page.render(_report(), backend_label="Claude Code")

    assert page.status_label.text() == "✓ 环境就绪：Claude Code 2.1.112 · bash 5.3.15 · shellcheck 0.11.0"
    assert "Claude Code: 2.1.112" in page.summary_text()
    assert page.backend_label() == "Claude Code"


def test_selfcheck_page_default_label_is_unchanged(qtbot):
    """不传后端名时的显示与加这个功能之前逐字一致（老用例与老用户都按这个读）。"""
    page = SelfCheckPage()
    qtbot.addWidget(page)

    page.render(_report(version="1.18.31"))

    assert page.status_label.text() == "✓ 环境就绪：opencode 1.18.31 · bash 5.3.15 · shellcheck 0.11.0"


def test_selfcheck_page_labels_a_missing_backend_by_its_name(qtbot):
    page = SelfCheckPage()
    qtbot.addWidget(page)

    page.render(
        DetectionReport(opencode=None, bash=None, shellcheck=None, problems=("未找到命令 claude",)),
        backend_label="Claude Code",
    )

    assert "Claude Code: 未找到" in page.summary_text()


# ── 控制器：按后端构建与探测 ──────────────────────────────────────────


def _window(qtbot, settings: AppSettings) -> MainWindow:
    window = MainWindow(wire_controller=False, settings=settings)
    qtbot.addWidget(window)
    return window


def test_injected_doubles_are_still_used_whatever_the_backend_is(qtbot, tmp_path):
    """注入替身时不许去碰注册表/探测（既有用例就是这么注入的，这条守住那个契约）。"""
    settings = AppSettings(
        run_root=str(tmp_path / "runs"),
        templates_dir=str(tmp_path / "templates"),
        agent_backend="claude",
    )
    opencode, toolchain = _FakeOpencode(), _FakeToolchain()
    controller = RunController(
        opencode=opencode, toolchain=toolchain, window=_window(qtbot, settings),
        settings=settings, run_root=str(tmp_path / "runs"),
    )

    assert controller._ensure_deps(controller.window.left_pane.to_run_config()) == (
        opencode,
        toolchain,
    )


def test_unknown_backend_in_settings_falls_back_to_the_default_backend(qtbot, tmp_path):
    settings = AppSettings(run_root=str(tmp_path / "runs"), templates_dir=str(tmp_path / "templates"))
    settings.agent_backend = "不存在的后端"          # 绕过 normalize 直接塞进去
    controller = RunController(
        window=_window(qtbot, settings), settings=settings, run_root=str(tmp_path / "runs")
    )

    assert controller._backend_id() == "opencode"
    assert controller._backend_label() == "opencode"


def test_controller_resolves_the_cli_backend_command_from_settings(qtbot, tmp_path):
    settings = AppSettings(
        run_root=str(tmp_path / "runs"),
        templates_dir=str(tmp_path / "templates"),
        agent_backend="claude",
        agent_command="/opt/claude",
    )
    controller = RunController(
        window=_window(qtbot, settings), settings=settings, run_root=str(tmp_path / "runs")
    )

    assert controller._backend_id() == "claude"
    assert controller._backend_label() == "Claude Code"
    assert controller._backend_command() == "/opt/claude"


def test_controller_builds_the_cli_adapter_and_keeps_bash_and_shellcheck(
    qtbot, tmp_path, bash_path, shellcheck_path
):
    """换成命令行后端：适配器跟着换，但执行侧仍是 bash + shellcheck（引擎才是执行者）。

    这条走真实路径（探测 + 装配），只有"agent 命令"是假的 —— 本机没有 codeagent，
    所以用可执行脚本扮演它。
    """
    settings = AppSettings(
        run_root=str(tmp_path / "runs"),
        templates_dir=str(tmp_path / "templates"),
        agent_backend="claude",
        agent_command=str(FIXTURE),
        bash_path=bash_path,
        shellcheck_path=shellcheck_path,
    )
    controller = RunController(
        window=_window(qtbot, settings), settings=settings, run_root=str(tmp_path / "runs")
    )

    adapter, toolchain = controller._ensure_deps(controller.window.left_pane.to_run_config())

    assert type(adapter).__name__ == "CliAgentAdapter"
    assert adapter._command == str(FIXTURE)
    # 引擎每轮开头都会调 detect()：它必须按当前后端算依赖，而不是仍然要求装了 opencode
    report = toolchain.detect()
    assert report.bash is not None and report.shellcheck is not None
    assert report.opencode is not None and report.opencode.version == "2.1.112"
    assert not any("opencode" in problem for problem in report.problems)
    adapter.dispose()


def test_selfcheck_worker_reports_the_cli_backend(
    qtbot, tmp_path, bash_path, shellcheck_path
):
    """自检页要按当前后端显示：命令行后端下第一栏是它的名字与版本，而不是 opencode。"""
    settings = AppSettings(
        run_root=str(tmp_path / "runs"),
        templates_dir=str(tmp_path / "templates"),
        agent_backend="claude",
        agent_command=str(FIXTURE),
        bash_path=bash_path,
        shellcheck_path=shellcheck_path,
    )
    window = _window(qtbot, settings)
    controller = RunController(window=window, settings=settings, run_root=str(tmp_path / "runs"))

    controller.recheck_environment()
    qtbot.waitUntil(lambda: window.selfcheck_page.report() is not None, timeout=20_000)

    page = window.selfcheck_page
    assert page.backend_label() == "Claude Code"
    assert "Claude Code 2.1.112" in page.status_label.text()
    assert "opencode" not in page.status_label.text()
    controller.shutdown()


def test_chat_model_list_is_refused_for_cli_backends(qtbot, tmp_path):
    settings = AppSettings(
        run_root=str(tmp_path / "runs"),
        templates_dir=str(tmp_path / "templates"),
        agent_backend="claude",
    )
    window = _window(qtbot, settings)
    controller = RunController(
        opencode=_FakeOpencode(), toolchain=_FakeToolchain(), window=window,
        settings=settings, run_root=str(tmp_path / "runs"),
    )

    controller._on_models_requested()

    assert "不提供模型列表" in window.chat_panel.status.text()


# ── 分层规矩 ──────────────────────────────────────────────────────────


def test_ui_layer_does_not_spawn_processes_or_speak_http():
    """`ui/**` 不得 import subprocess/httpx：外部世界只能在适配器层。

    这条规矩本来就是硬约束（子进程只在适配器层起），而这个功能又新加了两个"看起来该在
    界面里跑命令"的地方（检测按钮、环境探测）—— 它们都走 worker + 注册表，所以这条用例
    应该保持绿色；一旦有人图省事在界面里 import subprocess，它会立刻转红。
    """
    repo = Path(__file__).resolve().parents[1]
    offenders: list[str] = []
    for path in sorted((repo / "tu_shell_agent" / "ui").rglob("*.py")):
        text = path.read_text(encoding="utf-8")
        for marker in ("import subprocess", "from subprocess", "import httpx", "from httpx"):
            if marker in text:
                offenders.append(f"{path.relative_to(repo)} 用了 {marker}")
    assert not offenders, "界面层出现了外部世界依赖：" + "；".join(offenders)


def test_full_run_with_the_cli_backend_succeeds(
    qtbot, tmp_path, bash_path, shellcheck_path, monkeypatch
):
    """端到端：设置里选命令行后端 → 引擎用它生成 → 契约/锚点校验 → shellcheck → bash 执行。

    这是"换后端"这件事的最终验收：只要中间任何一环仍写死 opencode（探测、适配器、agent 文件、
    返回值的解析），这条用例就会失败。agent 由可执行脚本扮演（本机没有 codeagent），
    引擎侧的 shellcheck 与 bash 都是真的。
    """
    settings = AppSettings(
        run_root=str(tmp_path / "runs"),
        templates_dir=str(tmp_path / "templates"),
        agent_backend="claude",
        agent_command=str(FIXTURE),
        bash_path=bash_path,
        shellcheck_path=shellcheck_path,
    )
    window = _window(qtbot, settings)
    controller = RunController(
        window=window, settings=settings, run_root=str(tmp_path / "runs"), auto_confirm=True
    )
    plan = tmp_path / "plan.md"
    plan.write_text("打印一行 fake-ok", encoding="utf-8")
    window.left_pane.set_plan(str(plan))
    # 记下假 CLI 收到的参数：证明这一轮的脚本**确实**是它产出的（否则这条用例可能
    # 因为别的原因碰巧变绿，而"换后端"这件事根本没走到）
    args_file = tmp_path / "fake-args.txt"
    monkeypatch.setenv("FAKE_AGENT_ARGS_FILE", str(args_file))

    with qtbot.waitSignal(controller.finished, timeout=60_000) as blocker:
        controller.start()

    assert args_file.is_file(), "命令行 agent 一次都没被调用"
    assert "--session-id" in args_file.read_text(encoding="utf-8")
    result = blocker.args[0]
    assert result.outcome == "succeeded", window.status_label.text()
    assert window.right_pane.output_view.toPlainText().strip() == "fake-ok"
    script = (Path(controller._run_dir) / "script.sh").read_text(encoding="utf-8")
    assert "# @@TU:BODY@@" in script          # 锚点仍在（契约校验是引擎侧照旧做的）
    assert "echo fake-ok" in script
    meta = json.loads((Path(controller._run_dir) / "meta.json").read_text(encoding="utf-8"))
    assert meta["outcome"] == "succeeded"
    assert meta["sessionId"]                       # 我们用 UUID 建的会话，id 落在 meta 里


# ── 模型：候选与"检测"按钮随后端变 ────────────────────────────────────


def test_model_candidates_follow_the_selected_backend(qtbot):
    """命令行后端没有"列出模型"的命令，但下拉里要有可选的候选，不能是空的。

    用户反馈："模型也无法选择和检测可用模型"。opencode 能列（`opencode models`），
    命令行后端不能列 —— 那就用后端自己声明的候选（claude 系是 sonnet/opus/haiku），
    并且允许直接输入完整模型名。
    """
    from tu_shell_agent.ui.pages.settings_page import SettingsPage

    page = SettingsPage()
    qtbot.addWidget(page)

    page.backend_combo.setCurrentIndex(page.backend_combo.findData("codeagent"))
    candidates = [page.model_combo.itemText(i) for i in range(page.model_combo.count())]
    assert "sonnet" in candidates and "opus" in candidates, f"命令行后端没有模型候选：{candidates}"
    assert page.model_combo.isEditable(), "必须可编辑：完整模型名要能直接输入"

    page.backend_combo.setCurrentIndex(page.backend_combo.findData("opencode"))
    assert page.model_combo.count() == 1, "opencode 的模型靠运行时列出，不该预置候选"


def test_detect_models_button_is_useful_on_a_cli_backend(qtbot, monkeypatch):
    """命令行后端点「检测可用模型」要给出候选与说明，而不是抛一句"不提供"就完事。

    也不能真去跑 `opencode models`（那会把"这台机器没装 opencode"报成"检测失败"）。
    """
    from tu_shell_agent.ui import engine_worker
    from tu_shell_agent.ui.pages.settings_page import SettingsPage

    started: list[object] = []
    monkeypatch.setattr(
        engine_worker.ModelsWorker, "start", lambda self: started.append(self), raising=False
    )

    page = SettingsPage()
    qtbot.addWidget(page)
    page.backend_combo.setCurrentIndex(page.backend_combo.findData("codeagent"))
    page._refresh_models()

    assert not started, "命令行后端不该去跑 opencode models"
    hint = page.model_hint.text()
    assert "可用候选" in hint and "sonnet" in hint
    assert page.model_combo.count() > 1


# ── 组件路径：opencode 那行随后端启用/禁用 ────────────────────────────


def test_opencode_path_row_follows_the_selected_backend(qtbot):
    """`opencode` 路径只在后端是 opencode 时才有意义 —— 其余后端要标出来并置灰。

    用户问过："组件路径也还是 opencode，那我要是选择 codeagent 呢？"
    """
    from tu_shell_agent.ui.pages.settings_page import SettingsPage

    page = SettingsPage()
    qtbot.addWidget(page)

    page.backend_combo.setCurrentIndex(page.backend_combo.findData("opencode"))
    assert page.opencode_path_edit.isEnabled()
    assert "opencode" in page.opencode_path_label.text()

    page.backend_combo.setCurrentIndex(page.backend_combo.findData("codeagent"))
    assert not page.opencode_path_edit.isEnabled(), "非 opencode 后端时这一行应当置灰"
    assert "仅" in page.opencode_path_label.text()
    hint = page.components_hint.text()
    assert "后端 agent" in hint, "说明要告诉用户 agent 命令在哪里设"
    assert "shellcheck" in hint and "任何后端都需要" in hint, "说明要讲清执行侧两件套仍然必需"


def test_chat_model_dropdown_offers_candidates_on_a_cli_backend(qtbot, tmp_path):
    """选了命令行后端之后，**对话面板的模型下拉里要有候选**（用户实测"没法选择模型"）。

    命令行后端没有"列出模型"的命令，旧实现只写了一句提示就返回 —— 下拉里空空如也，
    用户只能猜模型名。候选由后端文件声明（claude 系是 sonnet/opus/haiku），
    并且仍然可以直接手输完整模型名。
    """
    settings = AppSettings(
        run_root=str(tmp_path / "runs"),
        templates_dir=str(tmp_path / "templates"),
        agent_backend="codeagent",
    )
    window = _window(qtbot, settings)
    controller = RunController(
        opencode=_FakeOpencode(), toolchain=_FakeToolchain(), window=window,
        settings=settings, run_root=str(tmp_path / "runs"),
    )
    chat = window.chat_panel

    controller._on_models_requested()

    items = [chat.model_combo.itemText(i) for i in range(chat.model_combo.count())]
    assert "sonnet" in items, f"对话面板的模型下拉里没有候选：{items}"
    assert chat.model_combo.isEditable(), "仍然要能手输完整模型名"
    assert "候选" in chat.status.text()

    # 选中一个候选 → 真的会用于这段对话
    chat.model_combo.setCurrentIndex(chat.model_combo.findText("opus"))
    assert controller._chat_model == "opus"


def test_unsaved_backend_selection_is_spelled_out(qtbot, tmp_path):
    """设置页里选了别的后端但没保存时，取模型要**如实说明**按哪个后端进行。

    用户实测报的"选了 codeagent，还是检测不了模型"就是这个：他改了设置页的下拉、没按保存，
    而运行与取模型都按**已保存**的后端走 —— 界面看着像已经切过去了，于是怎么点都取不到。
    """
    settings = AppSettings(
        run_root=str(tmp_path / "runs"),
        templates_dir=str(tmp_path / "templates"),
        agent_backend="opencode",
    )
    window = _window(qtbot, settings)
    controller = RunController(
        opencode=_FakeOpencode(), toolchain=_FakeToolchain(), window=window,
        settings=settings, run_root=str(tmp_path / "runs"),
    )
    page = window.settings_page

    # 在设置页把下拉切到 codeagent，但**不保存**
    page.backend_combo.setCurrentIndex(page.backend_combo.findData("codeagent"))
    page._refresh_backend_hint()
    assert "尚未保存" in page.backend_hint.text(), "设置页没有提示改完要保存"

    controller._on_models_requested()

    status = window.chat_panel.status.text()
    assert "还没保存" in status or "尚未保存" in status, f"没有说明未保存：{status}"
    # 而且不能去跑 opencode 的取模型（那会报"找不到 opencode"这种误导性的错）
    assert controller._models_worker is None or not controller._models_worker.isRunning()


def test_empty_model_list_is_reported_clearly(qtbot, tmp_path):
    """取到 0 个模型也要给一句能行动的话，不能卡在"正在获取…"。"""
    settings = AppSettings(
        run_root=str(tmp_path / "runs"),
        templates_dir=str(tmp_path / "templates"),
        agent_backend="opencode",
    )
    window = _window(qtbot, settings)
    controller = RunController(
        opencode=_FakeOpencode(), toolchain=_FakeToolchain(), window=window,
        settings=settings, run_root=str(tmp_path / "runs"),
    )
    controller._on_models_requested()
    controller._models_worker.done.emit([])          # 模拟后端返回空列表
    qtbot.wait(20)

    status = window.chat_panel.status.text()
    assert "没有取到任何模型" in status, f"空列表没有明确说明：{status}"
    assert "手输" in status or "登录" in status, "要给出下一步（登录 / 手输）"
