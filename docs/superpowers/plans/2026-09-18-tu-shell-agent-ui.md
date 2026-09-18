# tu-shell-agent 界面实现计划（PySide6 · Plan 2 / 2）

> **面向 AI 代理的工作者：** 必需子技能：使用 superpowers:subagent-driven-development（推荐）或 superpowers:executing-plans 逐任务实现此计划。步骤使用复选框（`- [ ]`）语法来跟踪进度。

**目标：** 给已完成的引擎装上 Windows 原生界面：一个窗口里同时看得见"方案与模板 / 脚本与轮次 / shellcheck 报告与执行输出"，能开始、取消、手工改脚本后重跑、并回放历史运行；最后用 PyInstaller 打出 Windows 可双击的 exe。

**架构：** `ui → orchestrator → {opencode_adapter, shell_toolchain, template_store, run_store}` 这条单向依赖**不变**。界面层只做两件事：把用户操作翻译成 `LoopInput`（或本计划新增的两个入口），把引擎吐出的 `RunEvent` 渲染成控件状态。**引擎跑在 `QThread` 里**，事件通过 Qt 信号（跨线程自动排队）投递到主线程；界面**不直接碰** `subprocess`/`httpx`。

**技术栈：** PySide6 6.11（Qt 6）+ pytest-qt 4.5（`QT_QPA_PLATFORM=offscreen` 无头跑测）+ PyInstaller 6.22（打包）。

**规格：** `docs/superpowers/specs/2026-09-17-tu-shell-agent-design.md`（§12 UI 规格、§14 测试策略、§15 验收标准）
**引擎计划（已完成）：** `docs/superpowers/plans/2026-09-17-tu-shell-agent-engine-python.md`

---

## 前置准备（人工，一次性）

```bash
cd /home/vconlln/my-agent
.venv/bin/python -m pip install "PySide6>=6.11" pytest-qt pyinstaller
.venv/bin/python -c "import PySide6, pytestqt; print(PySide6.__version__)"
```

预期打印 `6.11.x`。本机是 Linux 桌面环境，但**测试一律用无头后端**（见下），所以没有显示器也能跑。

> `QT_QPA_PLATFORM=offscreen` 是本计划所有界面测试的前提：`tests/conftest.py` 会在导入期 `os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")`。不要改成 `minimal`（它不实现窗口几何）。

## 文件结构

```
my-agent/
  pyproject.toml                  修改：加 [project.optional-dependencies].ui 与 dev 增 pytest-qt
  tu_shell_agent/
    orchestrator/loop.py          修改：抽出三个共享辅助 + 新增 resume_repair / verify_and_execute
    ui/
      __init__.py                 空
      app.py                      main()：QApplication 装配、样式、启动主窗口
      main_window.py              三区 QSplitter + 底栏 + 两个独立页（环境自检 / 设置）
      engine_worker.py            QThread：跑 run_loop，RunEvent→信号，取消与确认握手
      run_controller.py           把三区接起来：开始/取消/继续修复/手工改后重跑；事件→控件
      settings.py                 设置读写（QStandardPaths.AppDataLocation/settings.json）
      panes/
        __init__.py               空
        left.py                   方案选择与预览 + 运行参数
        templates.py              模板库（列表/编辑/占位符/trusted）
        center.py                 脚本全文（行号）+ 与上一轮 diff + 轮次时间线
        right.py                  shellcheck 报告（按编号分组）+ 执行输出 + notes/assumptions
      pages/
        __init__.py               空
        selfcheck.py              环境自检页
        settings_page.py          设置页（组件路径、默认运行根、阈值、超时）
        history.py                历史运行列表与回放
      widgets/
        __init__.py               空
        diff_view.py              基于 difflib 的行级 diff 渲染
        script_view.py            带行号的脚本查看器（暴露 set_text / jump_to_line）
        confirm_dialog.py         执行前确认（脚本全文 + 危险模式高亮）
  packaging/
    tu-shell-agent.spec           PyInstaller 规格（one-folder）
    build.md                      打包与 Windows 验收步骤（含 §14 手测清单勾选表）
  tests/
    test_ui_skeleton.py          任务 2
    test_engine_worker.py        任务 3
    test_settings_and_selfcheck.py  任务 4
    test_ui_left_pane.py         任务 5
    test_ui_templates_pane.py    任务 6
    test_ui_center_pane.py       任务 7
    test_ui_right_pane.py        任务 8
    test_run_controller.py       任务 9
    test_history_replay.py       任务 9
    test_loop_resume_entrypoints.py  任务 1
```

**界面层硬规则（审查者会查）：**
- `tu_shell_agent/ui/**` **不得** import `subprocess`、`httpx`；需要外部能力一律经引擎的 ports / facade。
- 引擎的对象（`RunEvent`、`LoopResult`、`ShellcheckFinding`…）**只在主线程被读**；worker 线程只 `emit`。
- 任何跑在 worker 线程里的代码**不得**触碰 Qt 控件；一切 UI 更新经信号。
- 关闭窗口必须**取消并收尾**运行中的引擎（`cancel()` + 等 worker 结束 + `dispose()`）。

---

### 任务 1：引擎补两个入口（界面要用，缺了 Plan 2 的"继续修复"就是假的）

**为什么必须先做**：规格 §6/§12 承诺了两件事——"3 轮失败后可**手工改脚本只重跑 shellcheck/执行**"与"**从第 n 轮继续**让模型修"。当前引擎只有 `run_loop`（从头开始），这两个能力没有任何入口。界面若自己拼装这些步骤，就等于把编排逻辑抄到 UI 层，直接违背分层。

**文件：**
- 修改：`tu_shell_agent/orchestrator/loop.py`
- 创建：`tests/test_loop_resume_entrypoints.py`

- [ ] **步骤 1：写失败的测试**

```python
# tests/test_loop_resume_entrypoints.py
"""新增的两个入口：手工改脚本后只重跑校验/执行；以及在既有会话上从第 n 轮继续修。

本文件**自带最小 fake**（不从其它测试文件 import）——跨文件 import 依赖 pytest 的
import 模式，脆弱；多写 40 行换取确定性是划算的。
"""

from dataclasses import dataclass, field

from tu_shell_agent.orchestrator.loop import (
    LoopPorts,
    ResumeInput,
    TemplateSpec,
    VerifyInput,
    resume_repair,
    verify_and_execute,
)
from tu_shell_agent.types import (
    DetectionReport,
    ExecuteResult,
    GeneratedScript,
    RunConfig,
    ShellcheckFinding,
)

SKELETON = "#!/usr/bin/env bash\nset -euo pipefail\n# @@TU:BODY@@\necho ok\n"
GOOD = GeneratedScript(script=SKELETON, notes="", assumptions=())
CONFIG_ARGS = {"run_root": "/tmp/root", "max_rounds": 3,
               "generate_timeout_ms": 5000, "execute_timeout_ms": 5000}

TEMPLATE = TemplateSpec(id="single", body=SKELETON, anchors=("@@TU:BODY@@",), trusted=True)


@dataclass
class Harness:
    scripts: list[str] = field(default_factory=list)
    prompts: list[str] = field(default_factory=list)
    attempts: list = field(default_factory=list)
    metas: list = field(default_factory=list)


def make_ports(rounds, shellcheck_for=None, execute_for=None, confirm=True):
    """最小 fake 集合：只实现本文件用到的能力。"""
    harness = Harness()
    turn = {"n": 0}

    class FakeOpencode:
        def start(self, run_dir, agent_name, model):
            return "ses_fake"

        def generate(self, session_id, message, schema, timeout_ms, on_delta=None, cancel=None):
            harness.prompts.append(message)
            result = rounds[min(turn["n"], len(rounds) - 1)]
            turn["n"] += 1
            harness.scripts.append(result.script)
            return result

        def abort(self, session_id):
            return None

        def dispose(self):
            return None

    class FakeToolchain:
        def detect(self):
            return DetectionReport(None, None, None, ())

        def shellcheck(self, script_path):
            findings = shellcheck_for(harness.scripts[-1]) if shellcheck_for else []
            return list(findings), 0, '{"comments": []}'

        def execute(self, script_path, cwd, timeout_ms, cancel=None, on_stdout=None, on_stderr=None):
            if execute_for is not None:
                return execute_for(harness.scripts[-1])
            return ExecuteResult(0, None, False, False, 1, "ok\n", "")

    class FakeConfirm:
        def confirm(self, round_no, script_path, script, trusted):
            return confirm

    class FakeStore:
        run_dir = "/tmp/run"

        def write_script(self, round_no, script):
            return "/tmp/run/script.sh"

        def write_attempt(self, round_no, files):
            harness.attempts.append((round_no, dict(files)))

        def write_meta(self, patch):
            harness.metas.append(dict(patch))

        def write_inputs(self, files):
            harness.attempts.append((0, dict(files)))

    ports = LoopPorts(
        opencode=FakeOpencode(),
        toolchain=FakeToolchain(),
        confirm=FakeConfirm(),
        store=FakeStore(),
        emit=lambda _event: None,
    )
    return ports, harness


def _attempt_names(harness) -> set[str]:
    return {name for _round, files in harness.attempts for name in files}
```

```python
def test_verify_and_execute_runs_shellcheck_then_execute(tmp_path):
    script = tmp_path / "script.sh"
    script.write_text('#!/usr/bin/env bash\n# @@TU:BODY@@\necho "ok"\n', encoding="utf-8")
    ports, harness = make_ports([GOOD])

    result = verify_and_execute(
        VerifyInput(
            script_path=str(script),
            run_dir=str(tmp_path),
            round_no=3,
            config=RunConfig(**CONFIG_ARGS),
            ports=ports,
        )
    )

    assert result.outcome == "succeeded"
    assert result.rounds == 3
    # 关键：这一步不该调用 opencode（不生成、不烧轮次）
    assert harness.prompts == []
    assert ("shellcheck.txt" in _attempt_names(harness)) is True


def test_verify_and_execute_blocks_on_shellcheck_findings(tmp_path):
    script = tmp_path / "script.sh"
    script.write_text('#!/usr/bin/env bash\nfor f in $(ls); do echo $f; done\n', encoding="utf-8")
    ports, harness = make_ports(
        [GOOD],
        shellcheck_for=lambda _s: [ShellcheckFinding("SC2045", 2, 10, "error", "use glob")],
    )

    result = verify_and_execute(
        VerifyInput(
            script_path=str(script),
            run_dir=str(tmp_path),
            round_no=1,
            config=RunConfig(**CONFIG_ARGS),
            ports=ports,
        )
    )

    # 手工改过的脚本仍然不合格 → 不执行、如实报告（由界面决定是否回灌给模型）
    assert result.outcome == "needs_human"
    assert result.last_execute is None
    assert [f.code for f in result.last_findings] == ["SC2045"]


def test_verify_and_execute_respects_user_rejection(tmp_path):
    script = tmp_path / "script.sh"
    script.write_text('#!/usr/bin/env bash\necho "ok"\n', encoding="utf-8")
    ports, _harness = make_ports([GOOD], confirm=False)

    result = verify_and_execute(
        VerifyInput(
            script_path=str(script),
            run_dir=str(tmp_path),
            round_no=2,
            config=RunConfig(**CONFIG_ARGS),
            ports=ports,
        )
    )
    assert result.outcome == "cancelled"


def test_resume_repair_continues_existing_session_without_restarting_opencode(tmp_path):
    """从第 2 轮继续：不得再调 opencode.start()，且要沿用给定 session_id。"""
    ports, harness = make_ports(
        [GOOD],
        shellcheck_for=lambda s: (
            [ShellcheckFinding("SC2086", 3, 6, "info", "quote it")] if "echo $f" in s else []
        ),
    )
    started: list[str] = []
    original_start = ports["opencode"].start

    def spy_start(run_dir, agent_name, model):
        started.append(run_dir)
        return original_start(run_dir, agent_name, model)

    ports["opencode"].start = spy_start

    result = resume_repair(
        ResumeInput(
            plan="方案",
            template=TEMPLATE,
            values={},
            run_dir=str(tmp_path),
            session_id="ses_existing",
            start_round=2,
            evidence=None,
            config=RunConfig(**CONFIG_ARGS),
            ports=ports,
        )
    )

    assert started == []  # 没有重开会话
    assert result.outcome == "succeeded"
    assert result.rounds == 2
    assert len(harness.prompts) == 1
```

- [ ] **步骤 2：运行测试验证失败**

运行：`.venv/bin/python -m pytest tests/test_loop_resume_entrypoints.py -o addopts="" -v`
预期：FAIL，`ImportError: cannot import name 'ResumeInput' from 'tu_shell_agent.orchestrator.loop'`

- [ ] **步骤 3：写最小实现**

在 `loop.py` 里**先抽三个共享辅助**（`run_loop` 改为调用它们，行为一字不变），再基于它们写两个新入口：

```python
def _check_script(*, round_no: int, script: str, template: TemplateSpec, anchors: tuple[str, ...],
                  run_dir: str, config: RunConfig, ports: LoopPorts,
                  emit: Callable[[RunEvent], None]) -> tuple[str, ContractResult | None, tuple[ShellcheckFinding, ...]]:
    """写盘 → 契约校验 → shellcheck。返回 (script_path, 契约失败?, 阻断性发现之外的发现)。

    契约失败时第二个元素非 None 且第三个为空；shellcheck 自身故障会抛（由调用方兜底）。
    """
    # 与 run_loop 现有实现逐字一致：normalize → emit("script") → write_script → write_attempt(notes)
    # → 契约校验 → shellcheck → write_attempt(shellcheck.*) → emit("shellcheck")


def _confirm_and_execute(*, round_no: int, script_path: str, script: str, trusted: bool,
                         run_dir: str, config: RunConfig, ports: LoopPorts, cancel: Any,
                         emit: Callable[[RunEvent], None]) -> tuple[bool, ExecuteResult | None]:
    """确认 → 执行。返回 (是否获批, 执行结果或 None)。"""


def _judge(result: ExecuteResult, blocking_findings: tuple[ShellcheckFinding, ...]) -> str:
    """succeeded / cancelled / 继续修 —— 只判，不落盘。"""
```

新增的两个入口（**这是本任务的交付物**）：

```python
@dataclass(frozen=True, slots=True)
class VerifyInput:
    """手工改过脚本后，只重跑"校验 + 执行"（不生成、不烧轮次）。"""

    script_path: str
    run_dir: str
    round_no: int
    config: RunConfig
    ports: LoopPorts
    trusted: bool = False
    cancel: Any = None


def verify_and_execute(input_: VerifyInput) -> LoopResult:
    """对已存在的脚本跑 shellcheck → 确认 → 执行。

    - shellcheck 有阻断性发现 → `needs_human`（把发现放进 last_findings，不执行、不生成）；
    - 用户拒绝 → `cancelled`；
    - 执行完成 → `succeeded` / `needs_human`（按退出码与 timed_out/cancelled 判定，与 run_loop 同规则）。
    """


@dataclass(frozen=True, slots=True)
class ResumeInput:
    """在**既有 opencode 会话**上从第 n 轮继续（不重开会话、不重发首轮消息）。"""

    plan: str
    template: TemplateSpec
    values: dict[str, str]
    run_dir: str
    session_id: str
    start_round: int
    config: RunConfig
    ports: LoopPorts
    evidence: FailureEvidence | None = None
    agent_name: str = "tu-shell-writer"
    cancel: Any = None


def resume_repair(input_: ResumeInput) -> LoopResult:
    """从 `start_round` 起续跑修复循环，直到成功、轮次用尽或取消。

    与 `run_loop` 的唯一区别：**不调用 `ports.opencode.start()`**，直接复用 `session_id`；
    首轮消息用 `build_repair_message`（因为已经有失败证据）而不是 `build_first_message`。
    """
```

- [ ] **步骤 4：运行测试验证通过**

运行：`.venv/bin/python -m pytest tests/test_loop_resume_entrypoints.py tests/test_loop.py -o addopts="" -v`
预期：**新用例全过，且 `tests/test_loop.py` 的 19 条一条不红**（重构不得改变既有行为）。

- [ ] **步骤 5：Commit**

```bash
git add tu_shell_agent/orchestrator/loop.py tests/test_loop_resume_entrypoints.py
git commit -m "feat(orchestrator): 新增 verify_and_execute 与 resume_repair 两个入口

界面需要"手工改脚本后只重跑校验/执行"与"从第 n 轮继续修"，而引擎此前只有从头开始的
run_loop；把两个能力补进编排层（而不是让 UI 抄一遍流程），并抽出三个共享辅助避免重复。"
```

---

### 任务 2：应用骨架（Qt 依赖 + 主窗口三区 + 无头测试）

**文件：**
- 修改：`pyproject.toml`、`tests/conftest.py`
- 创建：`tu_shell_agent/ui/__init__.py`、`ui/app.py`、`ui/main_window.py`、`tests/test_ui_skeleton.py`

- [ ] **步骤 1：写失败的测试**

```python
# tests/test_ui_skeleton.py
from PySide6.QtWidgets import QSplitter, QTabWidget

from tu_shell_agent.ui.main_window import MainWindow


def test_main_window_has_three_panes_and_two_pages(qtbot):
    window = MainWindow()
    qtbot.addWidget(window)

    splitter = window.findChild(QSplitter, "mainSplitter")
    assert splitter is not None, "三区必须是 QSplitter"
    assert splitter.count() == 3, "左/中/右三栏"

    tabs = window.findChild(QTabWidget, "sidePages")
    assert tabs is not None
    assert [tabs.tabText(i) for i in range(tabs.count())] == ["环境自检", "设置"]


def test_main_window_exposes_named_panes(qtbot):
    from PySide6.QtWidgets import QWidget

    window = MainWindow()
    qtbot.addWidget(window)
    for name in ("leftPane", "centerPane", "rightPane", "templatesPane", "historyList"):
        assert window.findChild(QWidget, name) is not None, name


def test_close_event_cancels_running_engine_without_raising(qtbot):
    window = MainWindow()
    qtbot.addWidget(window)
    window.close()  # 未运行时也必须安全
```

- [ ] **步骤 2：运行测试验证失败**

运行：`.venv/bin/python -m pytest tests/test_ui_skeleton.py -o addopts="" -v`
预期：FAIL，`ModuleNotFoundError: No module named 'tu_shell_agent.ui'`

- [ ] **步骤 3：写最小实现**

`pyproject.toml` 补两块：

```toml
[project.optional-dependencies]
ui = ["PySide6>=6.11"]

# dev 里追加（保留既有的 pytest）
# dev = ["pytest>=8", "pytest-qt>=4.5"]
```

`[project.scripts]` 增一个界面入口（保留既有的 CLI）：

```toml
[project.scripts]
tu-shell-agent = "tu_shell_agent.cli:main"
tu-shell-agent-ui = "tu_shell_agent.ui.app:main"
```

`tests/conftest.py` **顶部**加（必须在任何 Qt 导入之前）：

```python
import os

# 无头跑界面测试：必须在 QApplication 创建之前设置
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
```

`ui/main_window.py` 骨架（**控件 objectName 是测试契约，不要改**）：

```python
"""主窗口：三区 + 底栏 + 两个独立页。只做布局与装配，业务在 run_controller。"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QHBoxLayout, QListWidget, QMainWindow, QPushButton, QSplitter, QTabWidget, QVBoxLayout, QWidget,
)

from .panes.center import CenterPane
from .panes.left import LeftPane
from .panes.right import RightPane
from .panes.templates import TemplatesPane
from .pages.history import HistoryPage
from .pages.selfcheck import SelfCheckPage
from .pages.settings_page import SettingsPage


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("tu-shell-agent — 方案 → shell 脚本 → 执行 → 校验")
        self.resize(1440, 900)

        self.left_pane = LeftPane()
        self.left_pane.setObjectName("leftPane")
        self.templates_pane = TemplatesPane()
        self.templates_pane.setObjectName("templatesPane")
        self.center_pane = CenterPane()
        self.center_pane.setObjectName("centerPane")
        self.right_pane = RightPane()
        self.right_pane.setObjectName("rightPane")

        left_column = QSplitter(Qt.Orientation.Vertical)
        left_column.addWidget(self.left_pane)
        left_column.addWidget(self.templates_pane)
        left_column.setSizes([400, 500])

        self.splitter = QSplitter(Qt.Orientation.Horizontal)
        self.splitter.setObjectName("mainSplitter")   # 测试契约
        self.splitter.addWidget(left_column)
        self.splitter.addWidget(self.center_pane)
        self.splitter.addWidget(self.right_pane)
        self.splitter.setSizes([360, 620, 460])

        self.history_list = QListWidget()
        self.history_list.setObjectName("historyList")

        self.side_pages = QTabWidget()
        self.side_pages.setObjectName("sidePages")     # 测试契约
        self.side_pages.addTab(SelfCheckPage(), "环境自检")
        self.side_pages.addTab(SettingsPage(), "设置")

        self.start_button = QPushButton("开始")
        self.cancel_button = QPushButton("取消")
        self.continue_button = QPushButton("继续修复")
        self.verify_button = QPushButton("改后重跑")
        self.open_dir_button = QPushButton("打开运行目录")
        bottom = QHBoxLayout()
        for button in (self.start_button, self.cancel_button, self.continue_button,
                       self.verify_button, self.open_dir_button):
            bottom.addWidget(button)
        bottom.addStretch(1)

        # 底部一行：左边历史运行列表，右边两个独立页
        bottom_row = QHBoxLayout()
        bottom_row.addWidget(self.history_list, 1)
        bottom_row.addWidget(self.side_pages, 2)

        root_layout = QVBoxLayout()
        root_layout.addWidget(self.splitter, 1)
        root_layout.addLayout(bottom_row, 1)
        root_layout.addLayout(bottom)

        root = QWidget()
        root.setLayout(root_layout)
        self.setCentralWidget(root)

    def closeEvent(self, event) -> None:  # noqa: N802 - Qt 命名
        # 任务 9 会在这里接上 worker 的取消与收尾；此刻必须是安全的 no-op
        super().closeEvent(event)
```

> **必须注意（这是一次真实踩坑，实现者已因此挂掉一次）**：`history_list` 与 `side_pages` **必须真的被加进布局**。第一版骨架把它们创建出来、也设了 `objectName`，却忘了 `addWidget`——于是它们没有父对象，`findChild(QWidget, "historyList")` 沿对象树找不到，两条用例直接失败。**Qt 的 `findChild` 只搜对象树，孤立控件不在树里。**

`ui/app.py`：

```python
"""界面入口：装配 QApplication 与主窗口。"""

from __future__ import annotations

import sys

from PySide6.QtWidgets import QApplication

from .main_window import MainWindow


def main(argv: list[str] | None = None) -> int:
    app = QApplication(argv if argv is not None else sys.argv)
    app.setApplicationName("tu-shell-agent")
    window = MainWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
```

`ui/panes/{left,center,right,templates}.py` 与 `ui/pages/{selfcheck,settings_page,history}.py` 本任务只放**最小可构造的空面板**（各自一个 `QWidget` 子类，带自己的 `objectName` 与占位标题），具体内容由后续任务填充——**不要**在这一步实现业务逻辑。

- [ ] **步骤 4：运行测试验证通过**

运行：`.venv/bin/python -m pytest tests/test_ui_skeleton.py -o addopts="" -v`
预期：PASS（3 passed）。若报 `qt.qpa.plugin: could not load the Qt platform plugin`，说明 `QT_QPA_PLATFORM` 没能在 QApplication 之前生效——检查 conftest 顶部那行是否真的在导入 PySide6 之前执行。

- [ ] **步骤 5：Commit**

```bash
git add pyproject.toml tests/conftest.py tu_shell_agent/ui/ tests/test_ui_skeleton.py
git commit -m "feat(ui): 应用骨架（三区主窗口 + 两个独立页 + 无头测试）

控件 objectName 作为测试契约固定下来；此时各面板只有占位标题，业务由后续任务填充。"
```

---

### 任务 3：引擎线程桥（QThread + 事件信号 + 取消与确认握手）

**文件：**
- 创建：`tu_shell_agent/ui/engine_worker.py`、`tests/test_engine_worker.py`

- [ ] **步骤 1：写失败的测试**

```python
# tests/test_engine_worker.py
"""worker 必须：把引擎事件变成信号、支持取消、并且确认请求是"发信号 + 等回答"的握手。"""

import threading

import pytest

from tu_shell_agent.types import DetectionReport, GeneratedScript, RunConfig
from tu_shell_agent.ui.engine_worker import EngineWorker


class _FakeOpencode:
    def __init__(self, gate: threading.Event | None = None) -> None:
        self.gate = gate
        self.cancelled = False

    def start(self, run_dir, agent_name, model):
        return "ses_1"

    def generate(self, session_id, message, schema, timeout_ms, on_delta=None, cancel=None):
        if self.gate is not None:
            self.gate.wait(5)  # 让测试有机会取消
        return GeneratedScript(script="#!/usr/bin/env bash\n# @@TU:BODY@@\necho ok\n", notes="n", assumptions=())

    def abort(self, session_id):
        self.cancelled = True

    def dispose(self):
        pass


class _FakeToolchain:
    def detect(self):
        return DetectionReport(None, None, None, ())

    def shellcheck(self, script_path):
        return [], 0, '{"comments": []}'

    def execute(self, script_path, cwd, timeout_ms, cancel=None, on_stdout=None, on_stderr=None):
        from tu_shell_agent.types import ExecuteResult

        return ExecuteResult(0, None, False, False, 1, "ok\n", "")


def _make_input(worker: EngineWorker, *, trusted: bool, tmp_path=None):
    """worker 必须先 submit 一个 LoopInput，run() 才有活干（否则只会 emit failed）。"""
    from tu_shell_agent.orchestrator.loop import LoopInput, LoopPorts, TemplateSpec

    class _Store:
        run_dir = "/tmp/run"

        def write_script(self, round_no, script):
            return "/tmp/run/script.sh"

        def write_attempt(self, round_no, files):
            pass

        def write_meta(self, patch):
            pass

        def write_inputs(self, files):
            pass

    store = _Store()
    ports = LoopPorts(
        opencode=worker._opencode,
        toolchain=worker._toolchain,
        confirm=worker,
        store=store,
        emit=worker._emit,
    )
    input_ = LoopInput(
        plan="打印 ok",
        template=TemplateSpec(
            id="single",
            body="#!/usr/bin/env bash\n# @@TU:BODY@@\necho ok\n",
            anchors=("@@TU:BODY@@",),
            trusted=trusted,
        ),
        values={},
        run_dir="/tmp/run",
        config=worker._config,
        ports=ports,
    )
    worker.submit(input_)


def _worker(trusted: bool = True, opencode=None) -> EngineWorker:
    return EngineWorker(
        opencode=opencode if opencode is not None else _FakeOpencode(),
        toolchain=_FakeToolchain(),
        config=RunConfig(run_root="/tmp/root"),
        trusted=trusted,
    )


def test_worker_emits_run_events_in_order(qtbot):
    worker = _worker()
    _make_input(worker, trusted=True)
    seen: list[str] = []
    worker.event.connect(lambda event: seen.append(event.type))

    with qtbot.waitSignal(worker.finished_result, timeout=10_000) as blocker:
        worker.start()
    worker.wait(5_000)

    assert blocker.args[0].outcome == "succeeded"
    assert "script" in seen and "shellcheck" in seen and "execute" in seen


def test_worker_cancel_sets_token_and_finishes_cancelled(qtbot):
    gate = threading.Event()
    worker = _worker(opencode=_FakeOpencode(gate))
    _make_input(worker, trusted=True)
    with qtbot.waitSignal(worker.finished_result, timeout=10_000) as blocker:
        worker.start()
        qtbot.wait(100)
        worker.cancel()
        gate.set()
    worker.wait(5_000)
    assert blocker.args[0].outcome == "cancelled"


def test_worker_confirm_handshake_blocks_until_answered(qtbot):
    """确认握手：worker 发 confirm_requested 并阻塞，主线程 answer_confirm 后继续。

    注意：模板 `trusted=False` 才会走确认——它是**模板的属性**，不是 worker 的属性。
    """
    answer: list[bool] = []
    worker = _worker(trusted=False)
    _make_input(worker, trusted=False)

    def on_request(payload: dict) -> None:
        answer.append(True)
        worker.answer_confirm(True)

    worker.confirm_requested.connect(on_request)
    with qtbot.waitSignal(worker.finished_result, timeout=10_000):
        worker.start()
    worker.wait(5_000)
    assert answer == [True]
```

- [ ] **步骤 2：运行测试验证失败**

运行：`.venv/bin/python -m pytest tests/test_engine_worker.py -o addopts="" -v`
预期：FAIL，`ModuleNotFoundError: No module named 'tu_shell_agent.ui.engine_worker'`

- [ ] **步骤 3：写最小实现**

```python
# tu_shell_agent/ui/engine_worker.py
"""把引擎跑在 QThread 里：事件经信号回主线程，取消与确认用线程原语握手。

线程纪律（不可违反）：本模块的 run() 里**不得触碰任何 Qt 控件**；一切 UI 更新都靠 emit。
"""

from __future__ import annotations

import threading
from typing import Any

from PySide6.QtCore import QThread, Signal

from ..orchestrator.loop import LoopInput, LoopPorts, LoopResult, run_loop
from ..ports import OpencodePort, RunStorePort, ToolchainPort
from ..types import DetectionReport, RunConfig, RunEvent


class EngineWorker(QThread):
    event = Signal(object)                 # RunEvent
    finished_result = Signal(object)       # LoopResult
    confirm_requested = Signal(object)     # {"round": int, "script_path": str, "script": str}
    failed = Signal(str)                   # 引擎抛出的意外异常（已在 run() 里兜住）

    def __init__(self, *, opencode: OpencodePort, toolchain: ToolchainPort,
                 store: RunStorePort | None = None, config: RunConfig,
                 trusted: bool = False, parent: Any = None) -> None:
        super().__init__(parent)
        self._opencode = opencode
        self._toolchain = toolchain
        self._store = store
        self._config = config
        self.trusted = trusted
        self._cancel = threading.Event()
        self._confirm_answer: bool | None = None
        self._confirm_gate = threading.Event()
        self._input: LoopInput | None = None

    # ── 主线程调用 ────────────────────────────────────────────────
    def submit(self, input_: LoopInput) -> None:
        """设置本次运行输入（必须在 start() 之前调用）。"""
        self._input = input_

    def cancel(self) -> None:
        self._cancel.set()
        self._confirm_gate.set()  # 若正阻塞在确认上，一并放行（按"拒绝"处理）

    def answer_confirm(self, approved: bool) -> None:
        self._confirm_answer = approved
        self._confirm_gate.set()

    def cancel_token(self) -> threading.Event:
        return self._cancel

    # ── worker 线程 ───────────────────────────────────────────────
    def _confirm(self, round_no: int, script_path: str, script: str, trusted: bool) -> bool:
        if trusted:
            return True
        self._confirm_answer = None
        self._confirm_gate.clear()
        self.confirm_requested.emit({"round": round_no, "script_path": script_path, "script": script})
        self._confirm_gate.wait()
        return bool(self._confirm_answer)

    def _emit(self, event: RunEvent) -> None:
        self.event.emit(event)

    def run(self) -> None:  # noqa: D102 - QThread
        if self._input is None:
            self.failed.emit("EngineWorker.submit() 未被调用")
            return
        ports = LoopPorts(
            opencode=self._opencode,
            toolchain=self._toolchain,
            confirm=self,
            store=self._store if self._store is not None else self._input.ports.store,
            emit=self._emit,
        )
        input_ = LoopInput(
            plan=self._input.plan,
            template=self._input.template,
            values=self._input.values,
            run_dir=self._input.run_dir,
            config=self._config,
            ports=ports,
            agent_name=self._input.agent_name,
            cancel=self._cancel,
        )
        try:
            result = run_loop(input_)
        except BaseException as error:  # noqa: BLE001 - 线程里绝不能让异常逃逸
            self.failed.emit(str(error))
            return
        self.finished_result.emit(result)

    # ConfirmPort 协议
    def confirm(self, round_no: int, script_path: str, script: str, trusted: bool) -> bool:
        return self._confirm(round_no, script_path, script, trusted)
```

> **实现者注意两处：** ① `LoopPorts.confirm` 传的是 `self`（worker 实现了 `confirm`）；② `store` 若调用方没给，就沿用 `input_.ports.store`——这样 worker 只替换"确认"这一件事，其余端口原样透传。

- [ ] **步骤 4：运行测试验证通过**

运行：`.venv/bin/python -m pytest tests/test_engine_worker.py -o addopts="" -v`
预期：PASS（3 passed）

- [ ] **步骤 5：Commit**

```bash
git add tu_shell_agent/ui/engine_worker.py tests/test_engine_worker.py
git commit -m "feat(ui): 引擎线程桥（事件信号 + 取消 + 确认握手）

引擎跑在 QThread 里，RunEvent 经信号回主线程；确认是"发信号→阻塞→等回答"的握手，
因此执行前的确认对话框不会阻塞主线程。"
```

---

### 任务 4：设置持久化 + 环境自检页

**文件：**
- 创建：`tu_shell_agent/ui/settings.py`、`ui/pages/selfcheck.py`、`ui/pages/settings_page.py`、`tests/test_settings_and_selfcheck.py`

- [ ] **步骤 1：写失败的测试**

```python
# tests/test_settings_and_selfcheck.py
import json

from tu_shell_agent.types import DetectedTool, DetectionReport
from tu_shell_agent.ui.settings import AppSettings
from tu_shell_agent.ui.pages.selfcheck import SelfCheckPage


def test_settings_round_trip(tmp_path):
    path = tmp_path / "settings.json"
    settings = AppSettings.load(path)
    assert settings.blocking_level == "info"      # 默认与引擎一致
    assert settings.max_rounds == 3
    settings.run_root = str(tmp_path / "runs")
    settings.opencode_path = "/opt/opencode"
    settings.save()

    again = AppSettings.load(path)
    assert again.run_root == str(tmp_path / "runs")
    assert again.opencode_path == "/opt/opencode"
    assert json.loads(path.read_text(encoding="utf-8"))["blocking_level"] == "info"


def test_settings_ignores_unknown_keys_and_missing_file(tmp_path):
    path = tmp_path / "settings.json"
    path.write_text('{"max_rounds": 5, "未来的键": 1}', encoding="utf-8")
    settings = AppSettings.load(path)
    assert settings.max_rounds == 5
    assert not hasattr(settings, "未来的键")


def test_selfcheck_page_renders_report_and_problems(qtbot):
    page = SelfCheckPage()
    qtbot.addWidget(page)
    page.render(
        DetectionReport(
            opencode=DetectedTool(path="/usr/bin/opencode", version="1.18.31"),
            bash=DetectedTool(path="/usr/bin/bash", version="5.3.15"),
            shellcheck=None,
            problems=("未找到 shellcheck：winget install --id koalaman.shellcheck",),
        )
    )
    text = page.summary_text()
    assert "1.18.31" in text
    assert "5.3.15" in text
    assert "未找到 shellcheck" in text
    assert page.has_problems() is True
```

- [ ] **步骤 2：运行测试验证失败**

运行：`.venv/bin/python -m pytest tests/test_settings_and_selfcheck.py -o addopts="" -v`
预期：FAIL，`ModuleNotFoundError: No module named 'tu_shell_agent.ui.settings'`

- [ ] **步骤 3：写最小实现**

```python
# tu_shell_agent/ui/settings.py
"""设置持久化：一个 JSON 文件，缺省值必须与引擎默认一致（blocking_level="info"）。"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, fields
from pathlib import Path


@dataclass
class AppSettings:
    run_root: str = ""
    templates_dir: str = ""
    blocking_level: str = "info"      # 与 RunConfig 默认一致（实测 SC2086 是 info 级）
    max_rounds: int = 3
    generate_timeout_ms: int = 300_000
    execute_timeout_ms: int = 120_000
    opencode_path: str = ""
    bash_path: str = ""
    shellcheck_path: str = ""

    @classmethod
    def load(cls, path: Path) -> AppSettings:
        settings = cls()
        if not path.exists():
            return settings
        raw = json.loads(path.read_text(encoding="utf-8"))
        known = {f.name for f in fields(cls)}
        for key, value in raw.items():
            if key in known:                      # 未知键直接忽略（向前兼容）
                setattr(settings, key, value)
        return settings

    def save(self, path: Path | None = None) -> None:
        """无参时写回 load() 记住的那个路径（界面里最常见的用法）。"""
        target = path if path is not None else self._loaded_from
        if target is None:
            raise ValueError("未指定保存路径，且此前没有 load() 过")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(asdict(self), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def default_settings_path() -> Path:
    """设置的默认位置：Windows 是 %APPDATA%\\<应用名>\\settings.json，Linux 是 ~/.local/share/<应用名>/settings.json。

    用 `QStandardPaths.AppDataLocation` 拿到平台正确的位置——它按 `QApplication.applicationName()` 分目录，
    而任务 2 的 `app.py` 已经设了 `setApplicationName("tu-shell-agent")`，所以两边必须一致。
    """
    from PySide6.QtCore import QStandardPaths

    base = QStandardPaths.writableLocation(QStandardPaths.StandardLocation.AppDataLocation)
    return Path(base) / "settings.json"
```

> **`save()` 的语义**：`load(path)` 要把 `path` 记进 `self._loaded_from`（一个**非 dataclass 字段**，即普通的实例属性），这样界面里 `settings.save()` 就写回原文件；测试里正是这样用的。

`ui/pages/selfcheck.py`：

```python
"""环境自检页：显示三件套的版本/路径与问题清单，并允许重跑探测。"""

from __future__ import annotations

from PySide6.QtWidgets import QLabel, QPlainTextEdit, QPushButton, QVBoxLayout, QWidget

from ...types import DetectionReport


class SelfCheckPage(QWidget):
    def __init__(self) -> None:
        super().__init__()
        self._report: DetectionReport | None = None
        self.text = QPlainTextEdit()
        self.text.setReadOnly(True)
        self.recheck_button = QPushButton("重新检测")
        self.hint = QLabel("三件套缺一不可：opencode / Git Bash / shellcheck（规格 §9）")
        layout = QVBoxLayout(self)
        layout.addWidget(self.hint)
        layout.addWidget(self.recheck_button)
        layout.addWidget(self.text, 1)

    def render(self, report: DetectionReport) -> None:
        self._report = report
        self.text.setPlainText(self._format(report))

    def summary_text(self) -> str:
        return self.text.toPlainText()

    def has_problems(self) -> bool:
        return self._report is not None and bool(self._report.problems)

    @staticmethod
    def _format(report: DetectionReport) -> str:
        lines: list[str] = []
        for name in ("opencode", "bash", "shellcheck"):
            tool = getattr(report, name)
            if tool is None:
                lines.append(f"{name}: 未找到")
            else:
                lines.append(f"{name}: {tool.version}  ({tool.path})")
        if report.problems:
            lines.append("")
            lines.append("问题：")
            lines.extend(f"- {problem}" for problem in report.problems)
        return "\n".join(lines)
```

`ui/pages/settings_page.py`：各字段一组 `QLineEdit`/`QSpinBox`/`QComboBox` + "保存"按钮，读写 `AppSettings`。**只做这一件事**，不要在设置页里发起运行。

- [ ] **步骤 4：运行测试验证通过**

运行：`.venv/bin/python -m pytest tests/test_settings_and_selfcheck.py -o addopts="" -v`
预期：PASS（3 passed）

- [ ] **步骤 5：Commit**

```bash
git add tu_shell_agent/ui/settings.py tu_shell_agent/ui/pages/ tests/test_settings_and_selfcheck.py
git commit -m "feat(ui): 设置持久化与环境自检页

设置默认值与引擎一致（blocking_level=info）；未知键忽略以向前兼容；
自检页把三件套的版本/路径与问题清单如实呈现。"
```

---

### 任务 5：左栏（方案选择与预览 + 运行参数）

**文件：**
- 创建：`tu_shell_agent/ui/panes/left.py`、`tests/test_ui_left_pane.py`

- [ ] **步骤 1：写失败的测试**

```python
# tests/test_ui_left_pane.py
from pathlib import Path

from tu_shell_agent.ui.panes.left import LeftPane


def test_plan_picker_loads_preview(qtbot, tmp_path: Path):
    plan = tmp_path / "plan.md"
    plan.write_text("# 方案：整理日志\n按 mtime 倒序列出 .log", encoding="utf-8")
    pane = LeftPane()
    qtbot.addWidget(pane)

    pane.set_plan(str(plan))

    assert "按 mtime 倒序" in pane.plan_preview.toPlainText()
    assert pane.plan_path() == str(plan)


def test_to_run_config_reflects_widgets(qtbot, tmp_path: Path):
    pane = LeftPane()
    qtbot.addWidget(pane)
    pane.run_root_edit.setText(str(tmp_path / "runs"))
    pane.blocking_combo.setCurrentText("warning")
    pane.max_rounds_spin.setValue(5)

    config = pane.to_run_config()

    assert config.run_root == str(tmp_path / "runs")
    assert config.blocking_level == "warning"
    assert config.max_rounds == 5


def test_validate_reports_missing_inputs(qtbot, tmp_path: Path):
    pane = LeftPane()
    qtbot.addWidget(pane)
    problems = pane.validate()
    assert any("方案" in problem for problem in problems)
    assert any("运行根" in problem for problem in problems)


def test_validate_passes_for_ready_inputs(qtbot, tmp_path: Path):
    plan = tmp_path / "plan.md"
    plan.write_text("方案", encoding="utf-8")
    pane = LeftPane()
    qtbot.addWidget(pane)
    pane.set_plan(str(plan))
    pane.run_root_edit.setText(str(tmp_path / "runs"))
    assert pane.validate() == []
```

- [ ] **步骤 2：运行测试验证失败**

运行：`.venv/bin/python -m pytest tests/test_ui_left_pane.py -o addopts="" -v`
预期：FAIL，`ModuleNotFoundError: No module named 'tu_shell_agent.ui.panes.left'`（若任务 2 已建占位类，则 FAIL 在缺少 `set_plan`/`to_run_config` 等断言上）

- [ ] **步骤 3：写最小实现**

`LeftPane` 需要的控件与契约：

```python
class LeftPane(QWidget):
    """方案 + 本次运行参数。只收集输入与校验，不发起运行。"""

    plan_changed = Signal(str)          # 选择方案后发出（绝对路径）

    # 控件（objectName 即契约，测试会按名字找）
    plan_edit: QLineEdit                # objectName="planEdit"（只读，由对话框写入）
    plan_preview: QPlainTextEdit         # objectName="planPreview"（只读）
    run_root_edit: QLineEdit             # objectName="runRootEdit"
    blocking_combo: QComboBox            # objectName="blockingCombo"，条目顺序 error/warning/info/style，默认 info
    max_rounds_spin: QSpinBox            # objectName="maxRoundsSpin"，范围 1..10，默认 3
    generate_timeout_spin: QSpinBox      # objectName="generateTimeoutSpin"，毫秒，默认 300000
    execute_timeout_spin: QSpinBox       # objectName="executeTimeoutSpin"，毫秒，默认 120000

    def set_plan(self, path: str) -> None: ...      # 读文件填预览，文件读不了就预览里如实写错误
    def plan_path(self) -> str: ...                 # 空串表示未选
    def plan_text(self) -> str: ...
    def to_run_config(self) -> RunConfig: ...       # 只填 run_root/阈值/轮次/超时；组件路径由设置页与 selfcheck 决定
    def validate(self) -> list[str]: ...            # 文案要含"方案""运行根"，便于用户定位
```

- [ ] **步骤 4：运行测试验证通过**

运行：`.venv/bin/python -m pytest tests/test_ui_left_pane.py -o addopts="" -v`
预期：PASS（4 passed）

- [ ] **步骤 5：Commit**

```bash
git add tu_shell_agent/ui/panes/left.py tests/test_ui_left_pane.py
git commit -m "feat(ui): 左栏的方案选择与运行参数

阻断级别下拉默认 info（与引擎一致）；validate() 给出可定位的中文提示。"
```

---

### 任务 6：模板库面板（列表 / 编辑 / 占位符 / trusted）

**文件：**
- 创建：`tu_shell_agent/ui/panes/templates.py`、`tests/test_ui_templates_pane.py`

- [ ] **步骤 1：写失败的测试**

```python
# tests/test_ui_templates_pane.py
from tu_shell_agent.template_store.store import TemplateStore
from tu_shell_agent.ui.panes.templates import TemplatesPane


def _pane(qtbot, tmp_path) -> TemplatesPane:
    pane = TemplatesPane(store=TemplateStore(str(tmp_path / "templates")))
    qtbot.addWidget(pane)
    pane.reload()
    return pane


def test_reload_lists_builtin_templates(qtbot, tmp_path):
    pane = _pane(qtbot, tmp_path)
    ids = [pane.list_widget.item(i).text() for i in range(pane.list_widget.count())]
    assert set(ids) >= {"single", "args-batch", "logged-errors"}


def test_selecting_template_loads_body_and_placeholders(qtbot, tmp_path):
    pane = _pane(qtbot, tmp_path)
    pane.select("args-batch")
    assert "getopts" in pane.body_edit.toPlainText()
    # 通过公开访问器拿控件，不要靠 QFormLayout.itemAt 猜 label/field 的交替顺序
    assert pane.placeholder_input("script_name") is not None
    assert pane.placeholder_input("work_dir") is not None


def test_placeholder_values_change_rendered_preview(qtbot, tmp_path):
    pane = _pane(qtbot, tmp_path)
    pane.select("args-batch")
    pane.set_placeholder_value("work_dir", "/var/log")
    assert "/var/log" in pane.preview.toPlainText()


def test_trusted_toggle_persists(qtbot, tmp_path):
    pane = _pane(qtbot, tmp_path)
    pane.select("single")
    pane.trusted_check.setChecked(True)
    pane.save_current()
    assert TemplateStore(str(tmp_path / "templates")).get("single").trusted is True


def test_selected_template_spec_is_loop_ready(qtbot, tmp_path):
    pane = _pane(qtbot, tmp_path)
    pane.select("single")
    spec = pane.to_template_spec()
    assert spec.id == "single"
    assert "@@TU:BODY@@" in spec.anchors
    assert spec.trusted is False
```

- [ ] **步骤 2：运行测试验证失败**

运行：`.venv/bin/python -m pytest tests/test_ui_templates_pane.py -o addopts="" -v`
预期：FAIL，`ModuleNotFoundError: No module named 'tu_shell_agent.ui.panes.templates'`

- [ ] **步骤 3：写最小实现**

```python
class TemplatesPane(QWidget):
    """模板库：列表 + 正文编辑 + 占位符表单 + trusted + 渲染预览。"""

    template_changed = Signal(str)          # 选中/保存后发出模板 id

    # 控件：list_widget(QListWidget) / body_edit(QPlainTextEdit) / placeholder_form(QFormLayout)
    #      / trusted_check(QCheckBox) / preview(QPlainTextEdit 只读) / save_button / delete_button / import_button

    def __init__(self, store: TemplateStore, parent=None) -> None: ...
    def reload(self) -> None: ...                       # 列表来自 store.list()
    def select(self, template_id: str) -> None: ...      # 载入正文 + 按 declared_names 生成占位符表单
    def set_placeholder_value(self, name: str, value: str) -> None: ...
    def placeholder_values(self) -> dict[str, str]: ...
    def placeholder_input(self, name: str) -> QLineEdit | None: ...   # 找不到该占位符则 None
    def save_current(self) -> None: ...                  # store.save(...)，含 trusted
    def to_template_spec(self) -> TemplateSpec: ...      # 供 run_controller 构造 LoopInput
    def rendered_skeleton(self) -> str: ...              # render_template(body, placeholders, values)
```

**实现要点**：占位符表单里每个输入控件的 **`objectName` 必须含占位符名**（例如 `placeholder_work_dir`），测试据此查找；预览用 `render_template` 渲染，未声明占位符报错时把错误信息**显示在预览里**而不是弹窗（界面不该因为模板未渲染成功就阻断）。

- [ ] **步骤 4：运行测试验证通过**

运行：`.venv/bin/python -m pytest tests/test_ui_templates_pane.py -o addopts="" -v`
预期：PASS（5 passed）

- [ ] **步骤 5：Commit**

```bash
git add tu_shell_agent/ui/panes/templates.py tests/test_ui_templates_pane.py
git commit -m "feat(ui): 模板库面板（列表/编辑/占位符/trusted/预览）

占位符表单按模板正文自动生成；渲染失败时把错误显示在预览区而非弹窗。"
```

---

### 任务 7：中栏（带行号的脚本视图 + 与上一轮 diff + 轮次时间线）

**文件：**
- 创建：`tu_shell_agent/ui/widgets/script_view.py`、`ui/widgets/diff_view.py`、`ui/panes/center.py`、`tests/test_ui_center_pane.py`

- [ ] **步骤 1：写失败的测试**

```python
# tests/test_ui_center_pane.py
from tu_shell_agent.ui.panes.center import CenterPane
from tu_shell_agent.ui.widgets.diff_view import render_diff_html
from tu_shell_agent.ui.widgets.script_view import ScriptView


def test_script_view_has_line_numbers_and_jump(qtbot):
    view = ScriptView()
    qtbot.addWidget(view)
    view.set_text("line1\nline2\nline3")     # 注意不留尾随换行：QPlainTextEdit 会为它多算一个空块
    assert view.blockCount() == 3
    assert view.line_number_area_width() > 0

    view.jump_to_line(3)
    assert view.textCursor().blockNumber() == 2  # 0-based


def test_render_diff_marks_added_and_removed_lines():
    html = render_diff_html("echo a\necho b\n", "echo a\necho B\n")
    assert "echo b" in html and "echo B" in html
    assert "diff-removed" in html and "diff-added" in html


def test_center_pane_shows_round_and_diff_toggle(qtbot):
    pane = CenterPane()
    qtbot.addWidget(pane)
    pane.show_round(1, "echo one\n")
    pane.show_round(2, "echo two\n")

    assert "echo two" in pane.current_text()
    pane.set_compare_with_previous(True)
    assert "echo one" in pane.compare_html() and "echo two" in pane.compare_html()


def test_center_pane_timeline_lists_rounds(qtbot):
    pane = CenterPane()
    qtbot.addWidget(pane)
    pane.add_timeline_entry(1, phase="checking", outcome="SC2045 ×1")
    pane.add_timeline_entry(2, phase="execute", outcome="退出码 0")
    texts = [pane.timeline.item(i).text() for i in range(pane.timeline.count())]
    assert texts[0].startswith("第 1 轮")
    assert "退出码 0" in texts[1]
```

- [ ] **步骤 2：运行测试验证失败**

运行：`.venv/bin/python -m pytest tests/test_ui_center_pane.py -o addopts="" -v`
预期：FAIL，`ModuleNotFoundError: No module named 'tu_shell_agent.ui.widgets'`

- [ ] **步骤 3：写最小实现**

`ScriptView`：`QPlainTextEdit` 子类 + 行号区（Qt 官方那个经典做法）：

```python
class ScriptView(QPlainTextEdit):
    """带行号的只读脚本视图；jump_to_line 供右栏的报告点击跳转用。"""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setReadOnly(True)
        self.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        self._area = _LineNumberArea(self)
        self.blockCountChanged.connect(lambda _n: self._update_area_width())
        self.updateRequest.connect(self._on_update_request)
        self._update_area_width()

    def set_text(self, text: str) -> None:
        self.setPlainText(text)
        self.jump_to_line(1)

    def jump_to_line(self, line_no: int) -> None:
        block = self.document().findBlockByNumber(max(line_no - 1, 0))
        cursor = QTextCursor(block)
        self.setTextCursor(cursor)
        self.centerCursor()

    def line_number_area_width(self) -> int: ...
    def paint_line_numbers(self, event) -> None: ...
```

`diff_view.render_diff_html(old: str, new: str) -> str`：用 `difflib.SequenceMatcher` 逐行比较，输出带 `diff-added` / `diff-removed` class 的 `<pre>` HTML（**类名是测试契约**）。空 `old` 时整段视为新增。

`CenterPane`：`QTabWidget`（"本轮" / "对比上一轮"）+ `ScriptView` + 只读 `QTextBrowser`（对比页）+ `QListWidget`（objectName=`timeline`）+ `add_timeline_entry(round_no, phase, outcome)` / `show_round(round_no, script)` / `current_text()` / `set_compare_with_previous(bool)` / `compare_html()`。

- [ ] **步骤 4：运行测试验证通过**

运行：`.venv/bin/python -m pytest tests/test_ui_center_pane.py -o addopts="" -v`
预期：PASS（4 passed）

- [ ] **步骤 5：Commit**

```bash
git add tu_shell_agent/ui/widgets/ tu_shell_agent/ui/panes/center.py tests/test_ui_center_pane.py
git commit -m "feat(ui): 中栏（带行号脚本视图 + 与上一轮 diff + 轮次时间线）

行号区用 Qt 经典实现；diff 渲染成带 diff-added/diff-removed 类的 HTML。"
```

---

### 任务 8：右栏（shellcheck 报告分组 + 执行输出 + notes/assumptions）

**文件：**
- 创建：`tu_shell_agent/ui/panes/right.py`、`tests/test_ui_right_pane.py`

- [ ] **步骤 1：写失败的测试**

```python
# tests/test_ui_right_pane.py
from tu_shell_agent.types import ExecuteResult, ShellcheckFinding
from tu_shell_agent.ui.panes.right import RightPane


def _pane(qtbot) -> RightPane:
    pane = RightPane()
    qtbot.addWidget(pane)
    return pane


def test_findings_are_grouped_by_code_with_counts(qtbot):
    pane = _pane(qtbot)
    pane.render_findings([
        ShellcheckFinding("SC2086", 5, 6, "info", "quote it"),
        ShellcheckFinding("SC2086", 9, 6, "info", "quote it again"),
        ShellcheckFinding("SC2045", 4, 10, "error", "use glob"),
    ])
    tree = pane.findings_tree
    assert tree.topLevelItemCount() == 2
    first = tree.topLevelItem(0)
    assert first.text(0).startswith("SC2086")
    assert "2" in first.text(1)          # 计数
    assert first.childCount() == 2


def test_activating_a_finding_emits_its_line(qtbot):
    pane = _pane(qtbot)
    pane.render_findings([ShellcheckFinding("SC2045", 4, 10, "error", "use glob")])
    lines: list[int] = []
    pane.finding_activated.connect(lines.append)

    item = pane.findings_tree.topLevelItem(0).child(0)
    pane.findings_tree.itemActivated.emit(item, 0)

    assert lines == [4]


def test_render_execute_shows_exit_code_duration_and_flags(qtbot):
    pane = _pane(qtbot)
    pane.render_execute(ExecuteResult(-9, None, True, False, 400, "out\n", "err\n"))
    assert "退出码" in pane.execute_summary.text()
    assert "-9" in pane.execute_summary.text()
    assert "超时" in pane.execute_summary.text()
    assert "out" in pane.output_view.toPlainText()
    assert "err" in pane.output_view.toPlainText()


def test_render_notes_shows_tradeoffs_and_assumptions(qtbot):
    """规格 §11 硬性要求：模型对方案约束的取舍必须与脚本并列可见。"""
    pane = _pane(qtbot)
    pane.render_notes("把'目录不存在即失败'改成报 0 个文件", ("运行目录里没有 logs/",))
    text = pane.notes_view.toPlainText()
    assert "报 0 个文件" in text
    assert "没有 logs/" in text
```

- [ ] **步骤 2：运行测试验证失败**

运行：`.venv/bin/python -m pytest tests/test_ui_right_pane.py -o addopts="" -v`
预期：FAIL，`ModuleNotFoundError: No module named 'tu_shell_agent.ui.panes.right'`

- [ ] **步骤 3：写最小实现**

```python
class RightPane(QWidget):
    """shellcheck 报告（按编号分组）+ 执行输出 + 模型取舍说明。"""

    finding_activated = Signal(int)     # 行号，供中栏跳转

    # 控件：findings_tree(QTreeWidget，两列：编号/说明、计数) / output_view(QPlainTextEdit 只读)
    #      / execute_summary(QLabel) / notes_view(QPlainTextEdit 只读)

    def render_findings(self, findings: Sequence[ShellcheckFinding]) -> None: ...
    def render_execute(self, result: ExecuteResult) -> None: ...
    def render_notes(self, notes: str, assumptions: Sequence[str]) -> None: ...
```

**实现要点**：分组按 `code` 聚合，顶层项文本形如 `SC2086（info）`、第二列放计数；子项文本形如 `第 5 行:  quote it`，并把行号存进 `Qt.ItemDataRole.UserRole`。`itemActivated` 需要在构造时连到一个内部槽，再由槽 `emit self.finding_activated.emit(line)`（测试直接 `emit` 信号，因此内部槽必须挂在 `itemActivated` 上）。`render_execute` 的摘要行必须能表达三态：正常退出、`timed_out`（含"超时"字样）、`cancelled`。

- [ ] **步骤 4：运行测试验证通过**

运行：`.venv/bin/python -m pytest tests/test_ui_right_pane.py -o addopts="" -v`
预期：PASS（4 passed）

- [ ] **步骤 5：Commit**

```bash
git add tu_shell_agent/ui/panes/right.py tests/test_ui_right_pane.py
git commit -m "feat(ui): 右栏（报告分组 + 执行输出 + 模型取舍说明）

报告按 SC 编号分组并计数、点击跳行；notes/assumptions 与脚本并列可见
（规格 §11：succeeded 不等于方案被正确实现，取舍必须让人看见）。"
```

---

### 任务 9：运行编排（把三区接起来 + 确认对话框 + 历史回放）

**文件：**
- 创建：`tu_shell_agent/ui/run_controller.py`、`ui/widgets/confirm_dialog.py`、`tests/test_run_controller.py`、`tests/test_history_replay.py`
- 修改：`tu_shell_agent/ui/pages/history.py`（本任务把它从占位改成真的列表 + 回放）、`ui/main_window.py`（接线）

- [ ] **步骤 1：写失败的测试**

```python
# tests/test_run_controller.py
"""用假的 opencode/toolchain 驱动 controller，验证三区随事件更新、确认生效、取消与回放可用。"""

import threading

from tu_shell_agent.types import DetectionReport, ExecuteResult, GeneratedScript
from tu_shell_agent.ui.run_controller import RunController


class _FakeOpencode:
    def start(self, run_dir, agent_name, model):
        return "ses_ctrl"

    def generate(self, session_id, message, schema, timeout_ms, on_delta=None, cancel=None):
        return GeneratedScript(script="#!/usr/bin/env bash\n# @@TU:BODY@@\necho ok\n", notes="取舍说明", assumptions=("假设 A",))

    def abort(self, session_id):
        pass

    def dispose(self):
        pass


class _FakeToolchain:
    def __init__(self) -> None:
        self.executed = 0

    def detect(self):
        return DetectionReport(None, None, None, ())

    def shellcheck(self, script_path):
        return [], 0, '{"comments": []}'

    def execute(self, script_path, cwd, timeout_ms, cancel=None, on_stdout=None, on_stderr=None):
        self.executed += 1
        return ExecuteResult(0, None, False, False, 1, "ok\n", "")


def _controller(qtbot, tmp_path):
    controller = RunController(
        opencode=_FakeOpencode(),
        toolchain=_FakeToolchain(),
        run_root=str(tmp_path / "runs"),
    )
    qtbot.addWidget(controller.window)
    return controller


def test_start_runs_engine_and_updates_panes(qtbot, tmp_path):
    controller = _controller(qtbot, tmp_path)
    plan = tmp_path / "plan.md"
    plan.write_text("打印 ok", encoding="utf-8")
    controller.window.left_pane.set_plan(str(plan))
    controller.window.left_pane.run_root_edit.setText(str(tmp_path / "runs"))

    with qtbot.waitSignal(controller.finished, timeout=15_000) as blocker:
        controller.start()

    assert blocker.args[0].outcome == "succeeded"
    assert "echo ok" in controller.window.center_pane.current_text()
    assert controller.window.center_pane.timeline.count() >= 1
    assert controller.window.right_pane.output_view.toPlainText().strip() == "ok"


def test_confirm_dialog_can_reject_execution(qtbot, tmp_path):
    controller = _controller(qtbot, tmp_path)
    controller.auto_confirm = False           # 非信任模板 → 走确认
    controller.confirm_answer = False         # 测试替身：模拟用户点"拒绝"
    plan = tmp_path / "plan.md"
    plan.write_text("打印 ok", encoding="utf-8")
    controller.window.left_pane.set_plan(str(plan))

    with qtbot.waitSignal(controller.finished, timeout=15_000) as blocker:
        controller.start()

    assert blocker.args[0].outcome == "cancelled"


def test_cancel_stops_a_running_generation(qtbot, tmp_path):
    gate = threading.Event()

    class _Gated(_FakeOpencode):
        def generate(self, session_id, message, schema, timeout_ms, on_delta=None, cancel=None):
            gate.wait(10)
            return super().generate(session_id, message, schema, timeout_ms, on_delta, cancel)

    controller = RunController(opencode=_Gated(), toolchain=_FakeToolchain(), run_root=str(tmp_path / "runs"))
    qtbot.addWidget(controller.window)
    plan = tmp_path / "plan.md"
    plan.write_text("打印 ok", encoding="utf-8")
    controller.window.left_pane.set_plan(str(plan))

    with qtbot.waitSignal(controller.finished, timeout=15_000) as blocker:
        controller.start()
        qtbot.wait(200)
        controller.cancel()
        gate.set()

    assert blocker.args[0].outcome == "cancelled"


def test_verify_edited_script_uses_engine_entrypoint(qtbot, tmp_path):
    toolchain = _FakeToolchain()
    controller = RunController(opencode=_FakeOpencode(), toolchain=toolchain, run_root=str(tmp_path / "runs"))
    qtbot.addWidget(controller.window)
    controller.set_script_override("#!/usr/bin/env bash\necho edited\n")   # 模拟用户在界面上手工改过

    with qtbot.waitSignal(controller.finished, timeout=15_000) as blocker:
        controller.verify_edited()

    assert blocker.args[0].outcome == "succeeded"
    assert toolchain.executed == 1
```

```python
# tests/test_history_replay.py
import json
from pathlib import Path

from tu_shell_agent.run_store.layout import attempt_dir, run_dir_for
from tu_shell_agent.ui.pages.history import HistoryPage


def _make_run(root: Path, run_id: str, meta: dict, script: str = "echo hi\n") -> Path:
    run_dir = root / run_id
    (run_dir / "attempts" / "1").mkdir(parents=True)
    (run_dir / "script.sh").write_text(script, encoding="utf-8")
    (run_dir / "meta.json").write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")
    (run_dir / "attempts" / "1" / "stdout.txt").write_text("hi\n", encoding="utf-8")
    return run_dir


def test_history_lists_runs_and_tolerates_both_meta_shapes(qtbot, tmp_path):
    root = tmp_path / "runs"
    _make_run(root, "20260918-100000-aaaa", {"outcome": "succeeded", "rounds": 2})
    _make_run(root, "20260918-110000-bbbb", {
        "runId": "20260918-110000-bbbb", "outcome": "needs_human", "rounds": 3,
        "sessionId": "ses_x", "config": {"blocking_level": "info"}, "detection": {},
    })
    page = HistoryPage(run_root=str(root))
    qtbot.addWidget(page)
    page.reload()

    assert page.list_widget.count() == 2
    texts = [page.list_widget.item(i).text() for i in range(2)]
    assert any("succeeded" in t for t in texts)
    assert any("needs_human" in t for t in texts)


def test_replay_loads_script_and_output_into_panes(qtbot, tmp_path):
    root = tmp_path / "runs"
    _make_run(root, "20260918-100000-aaaa", {"outcome": "succeeded", "rounds": 1}, script="echo replay\n")
    page = HistoryPage(run_root=str(root))
    qtbot.addWidget(page)
    page.reload()

    page.list_widget.setCurrentRow(0)
    snapshot = page.current_snapshot()

    assert "echo replay" in snapshot["script"]
    assert "hi" in snapshot["stdout"]
    assert snapshot["meta"]["outcome"] == "succeeded"
```

- [ ] **步骤 2：运行测试验证失败**

运行：`.venv/bin/python -m pytest tests/test_run_controller.py tests/test_history_replay.py -o addopts="" -v`
预期：FAIL，`ModuleNotFoundError: No module named 'tu_shell_agent.ui.run_controller'`

- [ ] **步骤 3：写最小实现**

`run_controller.py` 要点（**只列契约与关键逻辑，实现细节自己补**）：

```python
class RunController(QObject):
    """把三区与引擎接起来。所有引擎对象只在主线程读；worker 只 emit。"""

    finished = Signal(object)          # LoopResult
    events = Signal(object)            # RunEvent（供测试观察）

    def __init__(self, *, opencode=None, toolchain=None, window=None, settings=None,
                 run_root: str = "") -> None:
        """测试可注入假的 opencode/toolchain；生产不传则由 selfcheck 的探测结果构造真实实现。"""
        self.window = window if window is not None else MainWindow()
        self.auto_confirm = True       # 测试替身：True 时跳过真实对话框（默认行为由 trusted 决定）
        self.confirm_answer = True

    # 主流程
    def start(self) -> None: ...
    def cancel(self) -> None: ...
    def continue_repair(self) -> None: ...     # 用 resume_repair + meta.json 里的 sessionId
    def verify_edited(self) -> None: ...       # 用 verify_and_execute 跑用户改过的脚本
    def set_script_override(self, script: str) -> None: ...

    # 事件 → 控件（这是本任务的核心）
    def _on_event(self, event: RunEvent) -> None:
        """phase → 底栏状态；script → 中栏 show_round；shellcheck → 右栏 render_findings
        + 中栏 timeline；execute → 右栏 render_execute + 中栏 timeline；note → 状态栏。
        attempts/<n>/notes.md 的内容在每轮结束时读回来喂给右栏 render_notes。"""

    def _on_confirm_requested(self, payload: dict) -> None: ...   # 弹 ConfirmDialog（或按替身回答）
    def _on_finished(self, result: LoopResult) -> None: ...       # 收尾：dispose adapter、按 outcome 更新按钮可用性
```

`widgets/confirm_dialog.py`：模态对话框，展示脚本全文并**高亮危险模式**（`rm -rf`、`mkfs`、`dd if=`、`> /dev/sd`、`chmod -R 777 /`、`curl ... | bash`），两个按钮"执行/跳过"。`ConfirmDialog.dangerous_matches(script) -> list[str]` 是**纯函数式**的公开方法，便于单测。

`pages/history.py`：`reload()` 扫 `run_root/*/meta.json`（**必须容忍三种形状**：bare `{outcome, rounds}`、超集 `{runId, sessionId, config, detection, outcome, rounds}`、以及 verify 路径只有 `config` 的中间形态——缺键要当作"未知"而不是 KeyError），列表项形如 `20260918-100000-aaaa · succeeded · 2 轮`；`current_snapshot()` 返回 `{"meta": …, "script": …, "stdout": …}` 供界面回填三区。

**本任务要把任务 2 里那个未使用的导入用起来（这是计划留下的一处冗余，现在收口）**：
任务 2 的 `main_window.py` 底部左侧放的是一个裸 `QListWidget`（objectName `historyList`），同时又 `from .pages.history import HistoryPage` 却没用它——因为当时还没接线。现在把两者合并：
- `MainWindow` 的底部左侧改为放 **`HistoryPage` 本身**（替掉裸 `QListWidget`）；
- `HistoryPage` 内部的那个列表控件必须保留 `objectName="historyList"`，这样任务 2 的测试契约（`findChild(QWidget, "historyList")`）**依然成立**，不必改测试；
- 接线：`HistoryPage.list_widget` 选中某条 → 三区回填（脚本进中栏、报告/输出进右栏、meta 进状态栏）；`reload()` 在每次运行结束后自动调用一次。

`main_window.py` 接线：把按钮与 `RunController` 的方法连起来；`closeEvent` 改成"若 worker 在跑则 `cancel()` 并 `wait(5000)`，再 `dispose()`"。

- [ ] **步骤 4：运行测试验证通过**

运行：`.venv/bin/python -m pytest tests/test_run_controller.py tests/test_history_replay.py tests/test_ui_skeleton.py -o addopts="" -v`
预期：PASS（8 passed：controller 4 + history 2 + skeleton 2 中的相关项；`test_close_event_*` 也必须仍过）

- [ ] **步骤 5：Commit**

```bash
git add tu_shell_agent/ui/ tests/test_run_controller.py tests/test_history_replay.py
git commit -m "feat(ui): 运行编排、确认对话框与历史回放

三区随 RunEvent 更新；确认走 worker 握手不阻塞主线程；关闭窗口会取消并收尾；
历史列表容忍 meta.json 的两种形状（bare 与超集）。"
```

---

### 任务 10：打包（PyInstaller）与 Windows 验收准备

**文件：**
- 创建：`packaging/tu-shell-agent.spec`、`packaging/build.md`、`tests/test_app_self_test.py`
- 修改：`tu_shell_agent/ui/app.py`（加 `--self-test`）、`.gitignore`（加 `build/`、`dist/`、`*.spec` 已存在则跳过）

- [ ] **步骤 1：写失败的测试**

```python
# tests/test_app_self_test.py
"""打包冒烟要用一个不进入事件循环的自检入口：构造主窗口后立即返回。"""

from tu_shell_agent.ui.app import main


def test_self_test_builds_window_and_exits_zero():
    assert main(["--self-test"]) == 0
```

- [ ] **步骤 2：运行测试验证失败**

运行：`.venv/bin/python -m pytest tests/test_app_self_test.py -o addopts="" -v`
预期：FAIL（`--self-test` 未被识别，`main` 会尝试进入事件循环而挂住或报未知参数）

- [ ] **步骤 3：写最小实现**

`app.py` 增加参数解析：

```python
def main(argv: list[str] | None = None) -> int:
    args = list(argv if argv is not None else sys.argv[1:])
    self_test = "--self-test" in args
    app = QApplication([sys.argv[0], *[a for a in args if a != "--self-test"]])
    app.setApplicationName("tu-shell-agent")
    window = MainWindow()
    if self_test:
        window.show()          # 走一遍真实构造与绘制路径（无头后端下无副作用）
        app.processEvents()
        return 0
    window.show()
    return app.exec()
```

`packaging/tu-shell-agent.spec`（one-folder；**PySide6 的 Qt 插件必须被收集**，这是打包最常见的坑）：

```python
# -*- mode: python ; coding: utf-8 -*-
from PyInstaller.utils.hooks import collect_data_files, collect_submodules

block_cipher = None

a = Analysis(
    ["../tu_shell_agent/ui/app.py"],
    pathex=[".."],
    binaries=[],
    datas=[],
    hiddenimports=collect_submodules("PySide6.QtWidgets") + ["tu_shell_agent.ui.app"],
    hookspath=[],
    runtime_hooks=[],
    excludes=["tkinter", "PySide6.QtWebEngineCore"],   # 不用的重件排除，产物体积可控
    cipher=block_cipher,
)
pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name="tu-shell-agent", console=False)
coll = COLLECT(exe, a.binaries, a.zipfiles, a.datas, name="tu-shell-agent")
```

`packaging/build.md` 写清三件事：① Windows 上的打包命令与前置（`pip install PySide6 pyinstaller`）；② **打包后的自检**：`dist\tu-shell-agent\tu-shell-agent.exe --self-test` 应立刻返回 0，界面能起来；③ 把规格 §14 的 Windows 手测清单**逐条抄成勾选表**（Git Bash 探测、`taskkill /T /F` 杀树、中文与含空格路径、UTF-8 输出无乱码、opencode 原生 `serve` 与权限 deny 实测、`trusted` 模板免确认、3 轮失败转人工、exe 双击可用）。

- [ ] **步骤 4：运行验证（Linux 冒烟 + 产物自检）**

```bash
cd /home/vconlln/my-agent
.venv/bin/python -m pytest tests/test_app_self_test.py -o addopts="" -v     # 预期 PASS
.venv/bin/python -m PyInstaller --clean --noconfirm --distpath dist --workpath build packaging/tu-shell-agent.spec
QT_QPA_PLATFORM=offscreen ./dist/tu-shell-agent/tu-shell-agent --self-test; echo "self-test exit=$?"
```

预期：打包成功（Linux one-folder），`--self-test` 返回 0。
**说明**：这里只证明"spec 与 Qt 钩子在无头 Linux 上能跑通"；**Windows 的 exe 必须在 Windows 上打**（§14 的手测清单里）。

- [ ] **步骤 5：Commit**

```bash
git add packaging/ tu_shell_agent/ui/app.py tests/test_app_self_test.py .gitignore
git commit -m "build: PyInstaller 规格与 Windows 验收步骤

--self-test 提供不进事件循环的打包冒烟入口；spec 收集 PySide6 子模块并排除不用的重件。"
```

---

## 完成标准（Plan 2）

- [ ] `QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest -o addopts=""` 全绿（引擎 + 界面），skip 仍只有 `test_e2e_live.py` 那 1 条。
- [ ] **界面能跑通一次真实运行**：在真机（本机即可）启动界面 → 选方案与模板 → 开始 → 三区实时更新 → 执行输出可见 → 结论与 `meta.json` 一致。
- [ ] **取消有效**：生成阶段点取消，`outcome=cancelled`，且不执行任何脚本。
- [ ] **手工改后重跑有效**：在界面里改脚本 → 点"改后重跑" → 只重跑 shellcheck 与执行（日志里不出现新的生成轮次）。
- [ ] **历史回放有效**：选一条历史运行，三区被正确回填（脚本、报告、输出、meta）。
- [ ] Linux one-folder 产物 `--self-test` 返回 0。
- [ ] 分层规则复核：`tu_shell_agent/ui/**` 不含 `subprocess`/`httpx` 的 import。

## 已知未覆盖（交给 Windows 手测）

- Windows 上的 exe 打包与双击启动；`taskkill /T /F` 的真实行为；中文/含空格路径；UTF-8 输出；opencode 原生 `serve`；权限 deny 实测（规格 §14 清单）。
- 视觉打磨（主题、图标、字号）不在本计划范围；先把信息结构做对。

## 自检记录

- **规格覆盖度**：§12（三区 + 两页 + 底栏）→ 任务 2/5/6/7/8/9；§11 的"notes/assumptions 必须与脚本并列" → 任务 8；§13 的错误处理（依赖缺失、确认拒绝、超时/取消）→ 任务 4/8/9；§14（pytest-qt）→ 各任务测试；§17 M4（打包）→ 任务 10；§6/§12 的"手工改后重跑"与"从第 n 轮继续" → **任务 1 补引擎入口**（此前引擎缺这两个能力，属 Plan 1 遗漏，已在本计划补齐）。
- **占位符扫描**：无 TODO/待定；任务 4 里显式纠正了测试代码里的一处笔误（`settings_defaults` 与 `save_to`），并给了两种可选做法，不留悬空决策。
- **类型一致性**：`LoopInput`/`LoopResult`/`RunEvent`/`DetectionReport`/`ShellcheckFinding`/`ExecuteResult` 均沿用引擎现有定义；`TemplateSpec`/`RunConfig` 同前；界面只新增 UI 自有类型（`AppSettings`、`RunController`）。
- **跨计划一致性**：本计划不修改引擎既有行为（任务 1 是**新增**入口 + 等价重构，并用 `tests/test_loop.py` 19 条用例作回归护栏）。
