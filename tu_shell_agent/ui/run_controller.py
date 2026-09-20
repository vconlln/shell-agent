"""运行编排：把三区、引擎线程、设置与历史接起来（规格 §12）。

两条纪律：

1. **界面不自己拼装编排步骤**。生成/执行/校验/修复一律走编排层的三个入口
   （`run_loop` / `resume_repair` / `verify_and_execute`），控制器只挑入口、给参数 ——
   界面里再写一遍"先校验再执行"就会和引擎的规则漂移（谁判 blocking、谁落 meta）。
2. **引擎只在 EngineWorker 线程里跑**。本控制器的所有方法都在主线程被调用，worker 只通过
   信号回来，所以这里可以直接摸控件，不需要额外加锁。
"""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from typing import Any, Callable

from PySide6.QtCore import QObject, QUrl, Signal
from PySide6.QtGui import QDesktopServices

from ..orchestrator.contract import extract_anchors
from ..orchestrator.loop import (
    LoopInput,
    LoopPorts,
    LoopResult,
    ResumeInput,
    TemplateSpec,
    VerifyInput,
    resume_repair,
    run_loop,
    verify_and_execute,
)
from ..run_store.layout import attempt_dir, error_evidence, make_run_id, run_dir_for
from ..run_store.store import RunStore
from ..template_store.builtins import BUILTIN_TEMPLATES
from ..template_store.render import PlaceholderSpec, declared_names, render_template
from ..types import (
    SEVERITY_RANK,
    DetectionReport,
    ExecuteEvidence,
    ExecuteResult,
    FailureEvidence,
    RunConfig,
    RunEvent,
)
from ..run_store.sessions import (
    append_chat,
    read_chat,
    scan_sessions,
    session_in,
    write_session_meta,
    write_session_model,
)
from .engine_worker import ChatWorker, DetectWorker, EngineWorker
from .widgets.confirm_dialog import ConfirmDialog

# 引擎写 notes.md 时用的分隔（见 orchestrator.loop 的 _check_script 调用点）。
_NOTES_SEPARATOR = "\n\n## 假设\n"

# 关窗时没能在超时内停下的线程挂在这里，由模块级列表持有强引用。
# Qt 的硬规则：QThread 在仍运行时被析构 = 进程 abort；保留引用是唯一不崩的选择。
_ORPHANS: list[Any] = []


def _split_notes(text: str) -> tuple[str, tuple[str, ...]]:
    """把 notes.md 拆成 (取舍说明, 假设列表)。

    规格 §11 要求这两样与脚本**并排**展示：`succeeded` 只说明"shellcheck 无阻断项 + 退出码 0"，
    不代表脚本遵守了方案里的约束。所以右栏必须能看到模型自己声明的前提，别让用户误以为
    "成功"等于"符合方案"。
    """
    if _NOTES_SEPARATOR not in text:
        return text.strip(), ()
    notes, _, rest = text.partition(_NOTES_SEPARATOR)
    assumptions = tuple(
        line[2:].strip() for line in rest.splitlines() if line.startswith("- ")
    )
    return notes.strip(), assumptions


def _builtin_template(template_id: str = "single") -> TemplateSpec:
    """模板库为空时的兜底：直接用内置模板构造 TemplateSpec。

    用户可能只想"选个方案就跑"，不必先碰模板库；没有兜底就会在 `to_template_spec()` 上抛异常。
    """
    item = next((t for t in BUILTIN_TEMPLATES if t.id == template_id), BUILTIN_TEMPLATES[0])
    placeholders = tuple(PlaceholderSpec(name) for name in declared_names(item.body))
    skeleton = render_template(item.body, list(placeholders), {})
    return TemplateSpec(
        id=item.id,
        body=item.body,
        anchors=extract_anchors(skeleton),
        trusted=False,
        placeholders=placeholders,
    )


class RunController(QObject):
    """三区 ↔ 引擎的接线员。"""

    finished = Signal(object)  # LoopResult；与 failed 二选一
    failed = Signal(str)       # 引擎层异常（连 LoopResult 都没产出）；与 finished 二选一
    events = Signal(object)    # RunEvent（同时转发给测试与需要观察的调用方）

    def __init__(
        self,
        *,
        opencode: Any = None,
        toolchain: Any = None,
        window: Any = None,
        settings: Any = None,
        run_root: str = "",
        auto_confirm: bool = True,
        confirm_answer: bool | None = True,
    ) -> None:
        """注入 opencode/toolchain 便是"不碰真实依赖"的模式（测试用）；不注入则现场探测三件套。

        `auto_confirm=True`（默认）表示**不弹真实对话框**，直接按 `confirm_answer` 回答 —— 这是
        测试替身，也是"连跑多次"时省掉每次点击的口子。生产界面在装配时传
        `auto_confirm=False, confirm_answer=None`：这样非 trusted 模板的每一次执行都会走
        `ConfirmDialog`（规格 §9 的人工闸门），trusted 模板由引擎自己跳过确认。
        """
        super().__init__()
        if window is None:
            # 延迟到函数内 import：main_window 装配时会 import 本模块来连按钮，
            # 模块级互相 import 会成环。
            from .main_window import MainWindow

            window = MainWindow()
        self.window = window
        self.settings = settings
        self.auto_confirm = auto_confirm
        self.confirm_answer = confirm_answer
        self._run_root = run_root
        self._opencode = opencode
        self._toolchain = toolchain
        self._adapter: Any = None

        self._worker: EngineWorker | None = None
        self._detect_worker: DetectWorker | None = None
        self._chat_worker: ChatWorker | None = None

        self._session_id = ""          # 当前 opencode 会话（续跑、对话共用同一个）
        self._chat_preamble = ""       # 只在新会话的第一句话前带上（方案上下文）
        self._run_dir = ""
        self._serve_dir = ""           # 当前 serve 起在哪个目录（会话按目录隔离）
        # 用户点了「新对话」：下一次提问**不要**又自动恢复最近那段（否则点了等于没点）
        self._force_new_chat = False
        self._chat_model = ""          # 这段对话用的模型（provider/model，空 = 沿用会话）
        self._config: RunConfig | None = None
        self._template: TemplateSpec | None = None
        self._plan_text = ""
        self._script_override: str | None = None
        self._last_result: LoopResult | None = None

        window.history_page.run_selected.connect(self._on_replay)
        self._connect_window(window)

    def _connect_window(self, window: Any) -> None:
        """把主窗口的按钮接到本控制器上。

        一个窗口同时只该有一个活跃控制器：窗口自己装配时会调它，而控制器"自带窗口"
        （测试里的默认路径）时也要接上同一套线 —— 否则测出来的按钮接线和跑起来的不是一套。
        `window.controller` 是 closeEvent 找收尾对象的入口。
        """
        window.controller = self
        window.start_button.clicked.connect(self.start)
        window.cancel_button.clicked.connect(self.cancel)
        window.continue_button.clicked.connect(self.continue_repair)
        window.verify_button.clicked.connect(self.verify_edited)
        window.open_dir_button.clicked.connect(self.open_run_dir)
        window.selfcheck_page.recheck_requested.connect(self.recheck_environment)
        # 右栏报表明说"双击条目跳到中栏对应行"（规格 §12）：不接这根线，那句话就是空头承诺。
        window.right_pane.finding_activated.connect(window.center_pane.jump_to_line)
        # 模型对话面板（对话只说话，不执行任何脚本）
        chat = window.chat_panel
        chat.send_requested.connect(self.ask)
        chat.cancel_requested.connect(self.cancel_chat)
        chat.script_extracted.connect(self._on_script_extracted)
        chat.session_selected.connect(self._on_session_selected)
        chat.model_changed.connect(self._on_chat_model_changed)
        chat.models_requested.connect(self._on_models_requested)
        chat.sessions_refresh_requested.connect(self.refresh_sessions)
        chat.new_session_requested.connect(self._on_new_session)
        window.set_running(False)

    # ── 环境自检 ──────────────────────────────────────────────────
    def recheck_environment(self) -> None:
        """按**当前后端**重跑环境探测并铺到自检页（规格 §9：缺一不可）。

        探测起子进程，所以走线程；结果只做展示 —— 真正的"能不能跑"由每次运行前的
        `_ensure_deps()` 现场再判一次（用户可能在自检之后把工具挪走）。

        依赖集合随后端而变：opencode 那条路要 opencode + bash + shellcheck，换成命令行后端
        之后 opencode 不再是依赖（装了别的 agent 的机器不该因为没装 opencode 就报红），
        但 bash 与 shellcheck 仍然必需 —— 引擎执行脚本用的是它们，换 agent 不换执行者。
        """
        if self._detect_worker is not None and self._detect_worker.isRunning():
            return
        worker = DetectWorker(
            self._path_overrides(),
            backend_id=self._backend_id(),
            command=self._backend_command_quietly(),
        )
        worker.done.connect(self._on_detected)
        worker.failed.connect(lambda message: self._status(f"环境探测失败：{message}"))
        self._detect_worker = worker
        self._status("正在检测环境…")
        worker.start()

    def _on_detected(self, report: DetectionReport) -> None:
        self.window.selfcheck_page.render(report, backend_label=self._backend_label())
        if report.problems:
            self._status(f"环境自检有 {len(report.problems)} 个问题（见「环境自检」页）")
        elif report.warnings:
            # 不能只说"通过"：无凭据时三项版本全绿、一生成就失败，这条提示是唯一的线索。
            self._status(f"环境自检通过，但有 {len(report.warnings)} 条提示（见「环境自检」页）")
        else:
            self._status("环境自检通过")

    # ── 对外动作 ──────────────────────────────────────────────────
    def start(self) -> None:
        """第 1 轮：方案 → 生成 → 校验 → 执行（失败回灌自修），全程在 worker 线程。"""
        if self._busy():
            self._status("上一次运行还没结束")
            return
        self._prefill_inputs()
        problems = self.window.left_pane.validate()
        if problems:
            self._status("输入还没齐：" + "；".join(problems))
            return

        template = self._template_spec()
        config = self._config_from_ui()
        plan_text = self.window.left_pane.plan_text()
        # 所有控件读取都必须在**主线程**完成后再交给 worker：Qt 控件不是线程安全的，
        # 在 worker 线程里调 text() 属于未定义行为（最坏是堆损坏，而不是一个可见异常）。
        values = self._placeholder_values()
        extra = self.window.left_pane.extra_instruction()
        run_dir = run_dir_for(config.run_root, make_run_id())
        RunStore(run_dir).init()

        self._run_dir = run_dir
        self._config = config
        self._template = template
        self._plan_text = plan_text

        def entry(ports: LoopPorts, cancel: Any) -> LoopResult:
            return run_loop(
                LoopInput(
                    plan=plan_text,
                    template=template,
                    values=values,
                    run_dir=run_dir,
                    config=config,
                    ports=ports,
                    cancel=cancel,
                    extra=extra,
                )
            )

        self._run(entry, run_dir, config)

    def continue_repair(self) -> None:
        """在**既有会话**上继续修（规格 §6）：不重开会话、不重发首轮消息。

        会话 id 从运行目录的 meta.json 读 —— 引擎在循环里建会话，界面拿不到，
        读盘同时也是"重启界面后仍能续跑"的唯一途径。
        """
        if self._busy():
            self._status("上一次运行还没结束")
            return
        meta = self._read_meta()
        session_id = meta.get("sessionId")
        if not session_id:
            self._status("该运行未记录会话，无法续跑。")
            return
        config = self._config_from_ui()
        run_dir = self._run_dir
        last_round = int(meta.get("rounds") or 0)
        template = self._template_spec()
        evidence = self._evidence_from_last_result(last_round)
        values = self._placeholder_values()   # 主线程读控件，理由同 start()

        def entry(ports: LoopPorts, cancel: Any) -> LoopResult:
            return resume_repair(
                ResumeInput(
                    plan=self._plan_text,
                    template=template,
                    values=values,
                    run_dir=run_dir,
                    session_id=session_id,
                    start_round=last_round + 1,
                    config=config,
                    ports=ports,
                    evidence=evidence,
                    cancel=cancel,
                )
            )

        # 续跑时中栏要看得见"正在修的是哪一份"：生成下一版可能要几十秒，
        # 中栏空着的话用户不知道自己在等什么。读盘取最后一轮落盘的脚本。
        repairing = self._last_script_on_disk(run_dir, last_round)
        self._run(
            entry,
            run_dir,
            config,
            resuming=True,
            initial_script=None if repairing is None else (last_round, repairing),
        )

    def verify_edited(self) -> None:
        """用户手工改过脚本后只重跑"校验 + 执行"（不生成、不烧轮次）。"""
        # 守卫必须在**任何写盘之前**：这个入口会往 run_dir 写 script.sh，
        # 若上一次运行还在跑，就会把正在跑的脚本换掉（引擎随后读到用户改的文本）。
        if self._busy():
            self._status("上一次运行还没结束")
            return
        script = self._script_override
        if script is None:
            # 没显式给过覆盖 → 就用中栏里显示的正文（用户可能直接在脚本视图里改的）。
            script = self.window.center_pane.current_text()
        if not script.strip():
            self._status("没有可校验的脚本")
            return

        config = self._config_from_ui()
        run_dir = self._run_dir or run_dir_for(config.run_root, make_run_id())
        store = RunStore(run_dir)
        store.init()
        round_no = max(int(self._read_meta(run_dir).get("rounds") or 0), 1)
        script_path = store.write_script(round_no, script)
        template = self._template_spec()

        self._run_dir = run_dir
        self._config = config
        self._template = template

        def entry(ports: LoopPorts, cancel: Any) -> LoopResult:
            return verify_and_execute(
                VerifyInput(
                    script_path=script_path,
                    run_dir=run_dir,
                    round_no=round_no,
                    config=config,
                    ports=ports,
                    trusted=template.trusted,
                    cancel=cancel,
                )
            )

        self._run(entry, run_dir, config, initial_script=(round_no, script))

    # ── 模型对话 ──────────────────────────────────────────────────────────
    def ask(self, message: str) -> None:
        """把一句话发给当前 opencode 会话，回复流式追加到对话记录里。

        没有会话时先建一个（`adapter.start` 会起 serve 并写本次运行的 agent 文件 ——
        权限收敛点不会因为"只是聊天"而被跳过）。新会话的第一句话会带上方案上下文，
        否则模型不知道这个项目在干什么。
        """
        chat = self.window.chat_panel
        if self._chat_worker is not None and self._chat_worker.isRunning():
            chat.add_note("上一条提问尚未返回，请等待或取消。")
            return

        if self._adapter is None and self._opencode is None:
            try:
                self._ensure_deps(self._config_from_ui())
            except _DependencyMissing as error:
                chat.add_error(str(error))
                return
        adapter = self._adapter if self._adapter is not None else self._opencode
        if adapter is None or not hasattr(adapter, "chat"):
            chat.add_error("当前 opencode 适配器不支持对话。")
            return

        # 没有会话时先看看**磁盘上**有没有可恢复的：对话目录与运行目录都记着 sessionId，
        # 重启不该等于"忘掉上次聊到哪"（用户明确要求按文件夹找回历史会话）。
        if not self._session_id and not self._force_new_chat:
            self._restore_latest_session()

        if not self._session_id:
            try:
                if self._adapter is not None:
                    run_dir = self._run_dir or self._new_chat_run_dir()
                    self._run_dir = run_dir
                    self._session_id = self._adapter.start(
                        run_dir, "tu-shell-writer", self._config_from_ui().model
                    )
                else:
                    run_dir = self._run_dir or self._new_chat_run_dir()
                    self._run_dir = run_dir
                    self._session_id = adapter.start(
                        run_dir, "tu-shell-writer", self._config_from_ui().model
                    )
                self._serve_dir = run_dir
                # 会话 id 与"这是一段对话"落进目录：重启后靠它把这段找回来
                write_session_meta(
                    run_dir,
                    self._session_id,
                    kind="chat",
                    model=str(self._config_from_ui().model or ""),
                )
            except Exception as error:  # noqa: BLE001 - 起不来就如实说
                chat.add_error(f"无法建立对话会话：{error}")
                return
            chat.set_status(f"对话会话：{self._session_id}")
            plan = self.window.left_pane.plan_text().strip()
            self._chat_preamble = (
                "（上下文）我在用这个工具把方案文档变成 shell 脚本。方案文档如下，"
                "接下来我会就脚本与报错向你提问：\n\n" + plan
                if plan
                else "（上下文）我在用这个工具把方案文档变成 shell 脚本，接下来会问你问题。"
            )
        else:
            self._chat_preamble = ""

        # 恢复出来的会话要保证 serve 起在**它自己的目录**里：opencode 的会话按项目目录隔离，
        # 在别的目录起 serve 会看不到那段会话。
        self._ensure_serve_for(self._run_dir)
        self._force_new_chat = False
        append_chat(self._run_dir, "user", message)

        # 没有指定过模型时，退回设置里那个（用户在下拉里的选择优先：那是显式选择）
        if not self._chat_model:
            self._chat_model = str(self._config_from_ui().model or "")
            if self._chat_model:
                self.window.chat_panel.set_model(self._chat_model)
        if self._run_dir and self._chat_model:
            # 记下这段对话实际用的模型：恢复时要把它带回下拉
            write_session_model(self._run_dir, self._chat_model)

        worker = ChatWorker(opencode=adapter, timeout_ms=self._config_from_ui().generate_timeout_ms)
        worker.submit(
            self._session_id,
            message,
            self._chat_preamble,
            self._chat_model or str(self._config_from_ui().model or ""),
        )
        self._chat_reply: list[str] = []
        worker.delta.connect(chat.append_delta)
        worker.done.connect(self._on_chat_done)
        worker.failed.connect(self._on_chat_failed)
        self._chat_worker = worker
        chat.set_busy(True)
        chat.begin_stream("模型回复")
        worker.start()

    def cancel_chat(self) -> None:
        if self._chat_worker is not None:
            self._chat_worker.cancel()

    # ── 历史会话（按目录检索）────────────────────────────────────────────
    def refresh_sessions(self) -> list:
        """扫运行根目录，把可恢复的会话铺进对话面板的下拉。"""
        sessions = scan_sessions(self._session_root())
        self.window.chat_panel.set_sessions(sessions, self._run_dir)
        return sessions

    def _session_root(self) -> str:
        return self._run_root or self._config_from_ui().run_root

    def _on_session_selected(self, run_dir: str) -> None:
        """切到某个目录的会话：连记录一起回填，接下来就在那段会话里继续问。"""
        reference = session_in(run_dir)
        chat = self.window.chat_panel
        if reference is None:
            chat.set_status("该目录未记录会话，无法恢复。")
            return
        self._run_dir = reference.run_dir
        self._session_id = reference.session_id
        self._chat_preamble = ""
        self._serve_dir = ""          # 目录换了，serve 要按新目录重起
        chat.load_history(read_chat(reference.run_dir))
        self._chat_model = reference.model or ""
        chat.set_model(self._chat_model)
        chat.set_status(f"已切换到 {reference.label()}（会话 {reference.session_id}）")

    def _on_new_session(self) -> None:
        """丢弃当前会话，下一条消息会开一段新对话。"""
        self._session_id = ""
        self._run_dir = ""
        self._chat_preamble = ""
        self._serve_dir = ""
        self._force_new_chat = True
        chat = self.window.chat_panel
        self._chat_model = ""
        chat.set_model("")
        chat.clear_history()
        chat.set_status("下一条提问将开始新对话。")

    def _restore_latest_session(self) -> bool:
        """没有任何会话时，自动接上磁盘上最近的一段**对话**（运行会话由运行本身接管）。"""
        for reference in scan_sessions(self._session_root()):
            if reference.kind == "chat":
                self._on_session_selected(reference.run_dir)
                return True
        return False

    def _ensure_serve_for(self, run_dir: str) -> None:
        """保证 serve 起在指定目录（opencode 的会话按项目目录隔离），不新建会话。"""
        adapter = self._adapter if self._adapter is not None else self._opencode
        resume = getattr(adapter, "resume", None)
        if adapter is None or resume is None or not run_dir or self._serve_dir == run_dir:
            return
        resume(run_dir, self._config_from_ui().model)
        self._serve_dir = run_dir

    def _on_chat_model_changed(self, model: str) -> None:
        """用户在下拉里换了模型：只影响这段对话，并写进目录（下次恢复还用它）。"""
        self._chat_model = model.strip()
        if self._run_dir and self._chat_model:
            write_session_model(self._run_dir, self._chat_model)
        self.window.chat_panel.set_status(
            f"这段对话将使用 {self._chat_model}。" if self._chat_model
            else "这段对话沿用会话自己的模型。"
        )

    def _on_models_requested(self) -> None:
        """拉可用模型列表填进对话面板的下拉（子进程调用放线程里）。

        **必须走 workers.track 托管**：直接挂在一个字段上，第二次请求就会把还在跑的
        那个覆盖掉，QThread 被 GC 时 Qt 直接 abort（用户报的"选模型直接闪退"）。
        """
        from .engine_worker import ModelsWorker
        from .workers import track

        chat = self.window.chat_panel
        if self._backend_id() != "opencode":
            # 模型列表只有 opencode 提供；命令行后端的模型名由用户自己填。照实说明，
            # 不去跑一条明知会失败的命令（那会把"没装 opencode"报成"取模型列表失败"）。
            chat.set_status("当前后端不提供模型列表，请在输入框旁直接填写模型名称。")
            return
        worker = ModelsWorker(str(getattr(self.settings, "opencode_path", "") or ""))
        worker.done.connect(lambda models: chat.set_models([str(item) for item in (models or [])]))
        worker.failed.connect(lambda message: chat.set_status(f"取可用模型失败：{message}"))
        track(worker)

    def _new_chat_run_dir(self) -> str:
        """为"先聊天、还没跑过"的情形准备一个运行目录（会话与 agent 文件需要落处）。"""
        config = self._config_from_ui()
        run_dir = run_dir_for(config.run_root, make_run_id())
        RunStore(run_dir).init()
        return run_dir

    def _on_chat_done(self, reply: str) -> None:
        chat = self.window.chat_panel
        if not reply.strip():
            chat.add_note("模型返回了空回复。")
        if self._run_dir:
            append_chat(self._run_dir, "model", reply)
        chat.set_busy(False)
        chat.set_status("回复中的脚本可经「把最新脚本放进中栏」进行改后重跑。")

    def _on_chat_failed(self, message: str) -> None:
        from ..opencode_adapter.errors import explain_provider_error

        chat = self.window.chat_panel
        hint = explain_provider_error(message)
        chat.add_error(f"对话失败：{message}" + (f"\n{hint}" if hint else ""))
        if self._run_dir:
            append_chat(self._run_dir, "error", message)
        chat.set_busy(False)
        chat.set_status("对话失败；上面是原始错误。" + ("已附上处理建议。" if hint else ""))

    def _on_script_extracted(self, script: str) -> None:
        """把对话里抠出来的脚本放进中栏——**只放进去，不执行**。

        之后它和手改的脚本走同一条路：shellcheck → 人工确认 → 执行。
        """
        round_no = max(int(self._read_meta().get("rounds") or 0), 1)
        self.window.center_pane.show_round(round_no, script)
        self.window.center_pane.tabs.setCurrentIndex(0)
        self._status("已把对话里的脚本放进中栏；要跑它请点「改后重跑」")

    def cancel(self) -> None:
        if self._worker is None:
            return
        self._status("正在取消…")
        self._worker.cancel()

    def set_script_override(self, script: str) -> None:
        """记下用户在界面上手工改过的脚本文本，供 `verify_edited()` 使用。"""
        self._script_override = script

    def open_run_dir(self) -> None:
        if self._run_dir:
            QDesktopServices.openUrl(QUrl.fromLocalFile(self._run_dir))

    # ── 线程装配 ──────────────────────────────────────────────────
    def _run(
        self,
        entry: Callable[[LoopPorts, Any], LoopResult],
        run_dir: str,
        config: RunConfig,
        *,
        resuming: bool = False,
        initial_script: tuple[int, str] | None = None,
    ) -> None:
        if self._busy():
            self._status("上一次运行还没结束")
            return
        try:
            opencode, toolchain = self._ensure_deps(config)
        except _DependencyMissing as error:
            self._status(str(error))
            return

        # 右栏那句"这一条会不会阻断"必须用**本次运行真正生效**的级别（引擎读的是同一份
        # config）。级别有两个来源（设置页存值、左栏本次运行值），生效的只有左栏那个。
        self.window.right_pane.blocking_level = config.blocking_level
        # 开跑前把三区里属于**上一次运行**的内容清掉：本次运行如果在生成阶段就失败
        # （无凭据、opencode 起不来都是常态），不会有任何 script/shellcheck/execute 事件，
        # 于是屏幕上会留着上一次的脚本、退出码 0 与 stdout —— 与本次"失败"并列，
        # 是最容易被读成"这次也成功了"的一种假象。
        self.window.center_pane.reset()
        self.window.right_pane.reset()
        if initial_script is not None:
            # 清屏是为了不留**上一次运行**的残留，但"这次要跑的那个脚本"必须留在屏幕上：
            # 改后重跑与续跑都不经过生成步骤，不会有 script 事件把它重新摆上来
            # （改后重跑尤其明显：用户刚在界面上改完脚本，一点按钮脚本就从眼前消失了）。
            round_no, script = initial_script
            self.window.center_pane.show_round(round_no, script)

        if resuming and self._adapter is not None:
            # 续跑不经过 run_loop 的 start()，所以适配器要在这里"续"起来：起 serve、
            # 重写本次运行的 agent 文件（权限收敛点），复用既有会话。不做这一步，
            # 第一次 generate 就会抛"适配器未启动"，被当成契约失败白烧轮次。
            try:
                self._adapter.resume(run_dir, config.model)
            except Exception as error:  # noqa: BLE001 - 起不来就是依赖问题，如实报
                self._dispose_adapter()
                self._status(f"续跑前无法启动 opencode：{error}")
                return

        store = RunStore(run_dir)
        worker = EngineWorker(opencode=opencode, toolchain=toolchain, config=config)
        # ports 的 confirm/emit 由 worker 线程补（确认必须回到主线程弹窗、事件必须经信号），
        # 这里传进去的 confirm/emit 只是为了满足 LoopPorts 的完整性，永远不会被调用到。
        worker.submit_entry(
            entry,
            LoopPorts(
                opencode=opencode,
                toolchain=toolchain,
                confirm=_NeverConfirm(),
                store=store,
                emit=_ignore,
            ),
        )
        # **先接信号再 start()**：opencode 起不来时 run_loop 会在几毫秒内返回，
        # 那时若槽还没连上，这一轮的结论就被静默丢掉——界面停在"开始运行…"、
        # 四个按钮永久禁用（只有"取消"亮着且点了没用），只能重启应用。
        # Qt 不会把已经发出的信号补发给后连的槽。
        worker.event.connect(self._on_event)
        worker.confirm_requested.connect(self._on_confirm_requested)
        worker.finished_result.connect(self._on_finished)
        worker.failed.connect(self._on_failed)
        self._worker = worker
        self._set_running(True)
        self._status("开始运行…")
        worker.start()

    def _ensure_deps(self, config: RunConfig):
        """返回 (agent 适配器, toolchain)。注入过替身就直接用；否则按设置里的后端现场构建。

        两条路的依赖不同，所以这里按后端分流（探测本身在 `agent_backends.detect_environment`）：
        opencode 要 opencode + bash + shellcheck；命令行后端只要"它的命令 + bash + shellcheck"。
        """
        del config                              # 端口签名要求，本方法用不到它
        if self._opencode is not None and self._toolchain is not None:
            return self._opencode, self._toolchain

        from ..agent_backends import (
            BackendError,
            backend_descriptor,
            build_adapter,
            detect_environment,
        )
        from ..shell_toolchain.detect import system_deps
        from ..shell_toolchain.facade import ShellToolchain

        overrides = self._path_overrides()
        try:
            backend_id = self._backend_id()
            descriptor = backend_descriptor(backend_id)
            command = self._backend_command(descriptor)
            report = detect_environment(backend_id, command, overrides=overrides)
        except BackendError as error:
            raise _DependencyMissing(str(error)) from error
        if report.problems:
            raise _DependencyMissing("环境自检未通过：" + "；".join(report.problems))
        assert report.bash and report.shellcheck

        # 走 serve 的后端（opencode）要用探测出来的**真实路径**：用户可能只在 PATH 里装了它，
        # 命令行后端则按用户填的命令启动（探测已经确认过解析得到）。
        resolved = report.opencode.path if descriptor.needs_serve and report.opencode else command
        try:
            self._adapter = build_adapter(backend_id, resolved, note=self._status)
        except BackendError as error:
            raise _DependencyMissing(str(error)) from error
        self._opencode = self._adapter
        # 三个 path 一起进 facade：run_loop 内部还会再 detect 一次，少了 override 会在
        # "shellcheck 不在 PATH"的机器上把已解析出的路径又判成缺失 → aborted_dependency。
        #
        # 探测钩子按**当前后端**注入：命令行后端不该因为"没装 opencode"就在预检处被拦下，
        # 而预检恰恰是每轮开头都会走的那一步（漏了它，换后端这个功能在没装 opencode 的
        # 机器上等于没生效，症状是每次运行都终止为 aborted_dependency）。
        self._toolchain = ShellToolchain(
            report.bash.path,
            report.shellcheck.path,
            overrides,
            detect=lambda: detect_environment(backend_id, command, overrides=overrides),
        )
        return self._opencode, self._toolchain

    # ── 后端选择（设置 → 后端 agent）────────────────────────────────
    def _backend_id(self) -> str:
        """设置里选的 agent 后端；取值非法时退回默认后端而不是抛异常。

        未知 id 在 `AppSettings.normalize()` 里已经收敛过一次，这里兜第二次是因为控制器也可能
        被喂进一个手工构造的设置对象：那时"报错"会让界面直接跑不起来，而退回默认后端至少是
        能跑的，且自检页与设置页会如实显示当前用的是谁。
        """
        from ..agent_backends import DEFAULT_BACKEND_ID, backend_descriptor

        raw = str(getattr(self.settings, "agent_backend", "") or "")
        try:
            backend_descriptor(raw)
        except Exception:  # noqa: BLE001 - 未知 id 不静默乱跑，退回默认后端
            return DEFAULT_BACKEND_ID
        return raw

    def _backend_command(self, descriptor: Any = None) -> str:
        """当前后端要执行的命令（与设置页提示行走同一个 `resolve_command`）。

        opencode 的路径也可以填在「组件路径」里（老字段），所以把它作为 fallback 传进去 ——
        优先级写在注册表一处，界面显示与实际执行才不会给出两个答案。
        """
        from ..agent_backends import backend_descriptor, resolve_command

        if descriptor is None:
            descriptor = backend_descriptor(self._backend_id())
        return resolve_command(
            descriptor.id,
            str(getattr(self.settings, "agent_command", "") or ""),
            fallback=str(getattr(self.settings, "opencode_path", "") or ""),
        )

    def _backend_command_quietly(self) -> str:
        """同上，但必填命令没填时返回空串（探测会把"没填"报成一条可读的问题）。"""
        try:
            return self._backend_command()
        except Exception:  # noqa: BLE001 - 探测路径上以"问题清单"的形式呈现
            return ""

    def _backend_label(self) -> str:
        """自检页第一栏显示的名字（当前后端的显示名）。"""
        from ..agent_backends import backend_descriptor

        try:
            return backend_descriptor(self._backend_id()).display_name
        except Exception:  # noqa: BLE001
            return "opencode"

    def _path_overrides(self) -> dict[str, str]:
        """设置页里的组件路径覆盖（绝对化，理由同 cli._path_overrides）。"""
        settings = self.settings
        if settings is None:
            return {}
        pairs = (
            ("opencode", getattr(settings, "opencode_path", "")),
            ("bash", getattr(settings, "bash_path", "")),
            ("shellcheck", getattr(settings, "shellcheck_path", "")),
        )
        return {
            tool: str(Path(path).expanduser().resolve()) for tool, path in pairs if path
        }

    # ── 输入装配 ──────────────────────────────────────────────────
    def _config_from_ui(self) -> RunConfig:
        config = self.window.left_pane.to_run_config()
        if not config.run_root:
            # 左栏没填就退到构造参数给的运行根（"改后重跑"常常没走左栏的校验）。
            # 用 replace 而不是 `RunConfig(**config.__dict__)`：RunConfig 是 slots 数据类，没有 __dict__。
            config = replace(config, run_root=self._run_root)
        # 模型来自设置页（provider/model）。**不能省**：留空时 opencode 若没配默认模型，
        # 会落到它自己的免费档，而免费档只允许官方客户端调用 —— 经 serve 的 API 调用会得到
        # "OpenCode's free tier can only be used from within OpenCode"。
        model = str(getattr(self.settings, "opencode_model", "") or "").strip()
        return replace(config, model=model or None)

    def _prefill_inputs(self) -> None:
        """把设置里的运行根填进左栏（只在用户还没填时），再交给 `validate()` 判。

        不预填的话，"选了方案、运行根靠设置"这种最常见的用法会被判成"运行根没填"，
        而用户其实早就配好了。
        """
        pane = self.window.left_pane
        if self._run_root and not pane.run_root_edit.text().strip():
            pane.run_root_edit.setText(self._run_root)

    def _template_spec(self) -> TemplateSpec:
        pane = getattr(self.window, "templates_pane", None)
        if pane is not None:
            try:
                spec = pane.to_template_spec()
            except Exception:  # noqa: BLE001 - 模板库还没选/为空时退回内置模板
                spec = None
            if spec is not None and spec.body.strip():
                return spec
        return _builtin_template()

    def _placeholder_values(self) -> dict[str, str]:
        pane = getattr(self.window, "templates_pane", None)
        if pane is None:
            return {}
        try:
            return dict(pane.placeholder_values())
        except Exception:  # noqa: BLE001 - 没有占位符表单就是没有值
            return {}

    def _evidence_from_last_result(self, last_round: int) -> FailureEvidence | None:
        """用上一轮的引擎结果构造修复证据；没有就返回 None（编排层会补空占位）。"""
        result = self._last_result
        if result is None:
            return None
        execute = result.last_execute
        return FailureEvidence(
            round=max(last_round, 1),
            stage="shellcheck" if result.last_findings else "execute",
            shellcheck=result.last_findings,
            execute=(
                None
                if execute is None
                else ExecuteEvidence(
                    exit_code=execute.exit_code,
                    timed_out=execute.timed_out,
                    stdout_tail=execute.stdout,
                    stderr_tail=execute.stderr,
                    duration_ms=execute.duration_ms,
                )
            ),
        )

    # ── 事件 → 控件 ───────────────────────────────────────────────
    def _on_event(self, event: RunEvent) -> None:
        center = self.window.center_pane
        right = self.window.right_pane
        payload = event.payload
        self.events.emit(event)

        if event.type == "phase":
            phase = payload.get("phase")
            if phase in {"confirming", "checking", "executing", "settled"}:
                self._stream_open = False   # 这一段输出结束，下次增量另起一段
            self._status(f"第 {event.round} 轮 · {phase}")
        elif event.type == "assistant_delta":
            # 增量文本原来是被丢掉的（只有状态栏一句"模型输出中…"）：生成一版要几十秒，
            # 那几十秒里用户看不到模型在写什么。现在流式追加到「模型对话」页签。
            chat = self.window.chat_panel
            if not getattr(self, "_stream_open", False):
                chat.begin_stream(f"第 {event.round} 轮 · 模型输出")
                self._stream_open = True
            chat.append_delta(payload.get("text") or "")
            self._status(f"第 {event.round} 轮 · 模型输出中…")
        elif event.type == "script":
            script = payload.get("script") or ""
            center.show_round(event.round, script)
            # 右栏整块清空：这一轮的脚本刚落地，还没校验、没执行。不清的话，中间某轮
            # 契约失败（只发 note、不发 shellcheck/execute）时，屏幕上会把**上一轮**的
            # 退出码与 stdout 摆在本轮脚本旁边，时间线也会把上一轮的退出码记到本轮名下。
            right.reset()
            self._status(f"第 {event.round} 轮 · 脚本已生成（{len(script.splitlines())} 行）")
        elif event.type == "shellcheck":
            findings = tuple(payload.get("findings") or ())
            right.render_findings(findings)
            right.render_notes(*self._read_notes(event.round))
            center.add_timeline_entry(
                event.round, phase="checking", outcome=f"{len(findings)} 条发现"
            )
        elif event.type == "execute":
            result = payload["result"]
            right.render_execute(result)
            center.add_timeline_entry(
                event.round, phase="execute", outcome=f"退出码 {result.exit_code}"
            )
        elif event.type == "note":
            self._status(f"第 {event.round} 轮 · {payload.get('message')}")

    def _on_confirm_requested(self, payload: dict) -> None:
        """worker 线程阻塞在确认关口上，这里必须给出答复（否则那个线程一直等）。"""
        approved = self._decide_confirm(payload)
        if self._worker is not None:
            self._worker.answer_confirm(approved)
        if not approved:
            self._status(f"第 {payload.get('round')} 轮：已跳过执行")

    def _decide_confirm(self, payload: dict) -> bool:
        if self.auto_confirm:
            # auto_confirm 的语义是"不问，直接批准"。注意 `confirm_answer=None` 表示
            # "没有预置答案"而不是"拒绝"：写成 bool(None) 会让"打开了自动确认但没给答案"
            # 变成每次都静默拒执行（实测：跑完只看到 cancelled，脚本一次都没跑）。
            return self.confirm_answer is not False
        if self.confirm_answer is not None:
            # 预置了答案（测试替身）：绝不能弹模态窗 —— 无头环境里 exec() 会一直等下去。
            return bool(self.confirm_answer)
        return ConfirmDialog.ask(
            int(payload.get("round") or 0),
            str(payload.get("script_path") or ""),
            str(payload.get("script") or ""),
            self.window,
        )

    def _on_finished(self, result: LoopResult) -> None:
        self._last_result = result
        self._session_id = str(self._read_meta().get("sessionId") or self._session_id)
        self._set_running(False)
        self._status(f"结论：{result.outcome}（{result.rounds} 轮）")
        self._dispose_adapter()
        self.window.history_page.reload()
        self._sync_buttons()
        if result.outcome != "succeeded":
            self._show_error_evidence(result)
        # 最后才发：调用方（测试、将来的批处理）收到这个信号时，界面与按钮状态已经收好。
        self.finished.emit(result)

    def _on_failed(self, message: str) -> None:
        """引擎层异常：没有 LoopResult 可给，所以走 failed 而不是 finished。

        两个信号**二选一**（每次运行恰好一个）。等待结论的调用方要同时听两个，
        否则引擎异常时它会一直等不到——这正是加这个信号的原因。
        """
        from ..opencode_adapter.errors import explain_provider_error

        self._set_running(False)
        # 生成阶段撞上 provider 报错时，同样给一句可操作的建议（与对话那条路一致）
        hint = explain_provider_error(message)
        self._status(f"引擎异常：{message}" + (f"\n{hint}" if hint else ""))
        self._dispose_adapter()
        self._sync_buttons()
        self.failed.emit(message)

    def _busy(self) -> bool:
        return self._worker is not None and self._worker.isRunning()

    def _on_replay(self, snapshot: dict) -> None:
        """历史回放：把某次运行的产物回填三区（只读展示，不影响正在跑的运行）。"""
        if not snapshot:
            return
        meta = snapshot.get("meta") or {}
        script = snapshot.get("script") or ""
        # 历史运行的会话也接上：opencode 的会话 id 就写在那次运行的 meta.json 里，
        # 于是"看一眼历史"之后可以直接就着那一段继续问（以前只有「继续修复」用它）。
        run_dir = str(snapshot.get("run_dir") or "")
        session_id = str(meta.get("sessionId") or "")
        if run_dir and session_id:
            chat_panel = self.window.chat_panel
            self._run_dir = run_dir
            self._session_id = session_id
            self._chat_preamble = ""
            self._serve_dir = ""
            chat_panel.load_history(read_chat(run_dir))
            chat_panel.set_status(f"当前会话：该运行的会话（{session_id}）")
        center = self.window.center_pane
        right = self.window.right_pane
        # 回放前必须先清空：否则上一次运行的轮次会被当成"这一轮的上一轮"，对比页会给出
        # 两次**不同运行**之间的假差异，时间线里也会留着别人的轮次。
        center.reset()
        right.reset()
        # 标注用的阻断级别要取**那次运行**记在 meta 里的值：用当前设置里的级别去标注
        # 历史报告，会把当时被阻断的发现标成"仅展示"，与当时的结论相反。
        recorded = (meta.get("config") or {}).get("blocking_level")
        if recorded in SEVERITY_RANK:
            right.blocking_level = recorded
        if script:
            center.show_round(int(meta.get("rounds") or 1), script)
        findings = snapshot.get("findings")
        if findings is not None:
            # None 表示"这份运行没留下 shellcheck 报告"（例如第 1 轮契约失败就退出了）：
            # 不能喂空元组，那会被右栏渲染成"报告：0 处（本轮没有发现）"——把"没有数据"
            # 说成"检查过了没问题"，是这份界面里最不该出现的假结论。
            right.render_findings(findings)
        execute = snapshot.get("execute")
        if isinstance(execute, dict):
            # 执行结论（退出码/超时/取消/耗时）必须一起回放：少了它，"这次到底跑没跑成"
            # 就无从判断，而 attempts/<n>/execute.json 里明明记着。
            right.render_execute(
                ExecuteResult(
                    exit_code=execute.get("exit_code"),
                    signal=None,
                    timed_out=bool(execute.get("timed_out")),
                    cancelled=bool(execute.get("cancelled")),
                    duration_ms=int(execute.get("duration_ms") or 0),
                    stdout=snapshot.get("stdout") or "",
                    stderr=snapshot.get("stderr") or "",
                )
            )
        notes, assumptions = _split_notes(snapshot.get("notes") or "")
        right.render_notes(notes, assumptions)
        errors = snapshot.get("errors") or ()
        if errors:
            # 失败运行没有脚本输出可看，把错误证据摊在输出区（带一行说明它是什么）：
            # 回放"为什么失败"是历史页存在的意义。
            right.output_view.setPlainText(
                "本次运行没有执行输出；以下是落盘的错误证据：\n\n"
                + "\n\n".join(f"── {name} ──\n{text}" for name, text in errors)
            )
        self._status(f"回放：{meta.get('outcome') or '未知'}（{meta.get('rounds', '?')} 轮）")

    # ── 小工具 ────────────────────────────────────────────────────
    def _show_error_evidence(self, result: LoopResult) -> None:
        """把这次运行的错误证据摊在输出区。

        失败时屏幕上原本只有一句"结论：needs_human"：中栏空、右栏"尚未校验"、输出空，
        而"为什么失败"（例如上游拒绝生成的那句原话）只躺在运行目录里。用户在界面上
        无从判断，只能去翻目录 —— 这正是实时路径与历史回放必须一致的地方。
        """
        evidence: list[tuple[str, str]] = []
        for round_no in range(max(result.rounds, 1), 0, -1):
            found = error_evidence(attempt_dir(self._run_dir, round_no))
            if found:
                evidence = [(f"第 {round_no} 轮 / {name}", text) for name, text in found]
                break                      # 只看最后那个有证据的轮次，别把三轮重复的贴一遍
        if not evidence:
            return
        self.window.right_pane.output_view.setPlainText(
            "本次运行没有执行输出；以下是落盘的错误证据：\n\n"
            + "\n\n".join(f"── {label} ──\n{text}" for label, text in evidence)
        )
        self._status(f"结论：{result.outcome}（{result.rounds} 轮）· 失败原因见下方输出区")

    def _last_script_on_disk(self, run_dir: str, round_no: int) -> str | None:
        """取最后一轮落盘的脚本内容（续跑时摆回中栏用）；没有就返回 None。"""
        if round_no <= 0:
            return None
        path = Path(attempt_dir(run_dir, round_no)) / "script.sh"
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            return None
        return text or None

    def _read_notes(self, round_no: int) -> tuple[str, tuple[str, ...]]:
        """读回本轮 notes.md（引擎在写脚本时一并落盘）。"""
        if not self._run_dir:
            return "", ()
        path = Path(self._run_dir) / "attempts" / str(round_no) / "notes.md"
        try:
            return _split_notes(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError):
            return "", ()

    def _read_meta(self, run_dir: str | None = None) -> dict:
        target = run_dir if run_dir is not None else self._run_dir
        if not target:
            return {}
        try:
            meta = json.loads((Path(target) / "meta.json").read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            return {}
        return meta if isinstance(meta, dict) else {}

    def _dispose_adapter(self) -> None:
        adapter = self._adapter
        self._adapter = None
        self._serve_dir = ""
        # 释放后必须把两个端口一起清掉：只清 adapter 会让下一次运行继续用已经关掉的
        # opencode 服务端（表现为"第二次运行莫名失败"）。
        if adapter is not None:
            self._opencode = None
            self._toolchain = None
            try:
                adapter.dispose()
            except Exception:  # noqa: BLE001 - 收尾失败不该盖住真正的运行结论
                pass

    def _status(self, text: str) -> None:
        window = self.window
        setter = getattr(window, "set_status", None)
        if callable(setter):
            setter(text)

    def _set_running(self, running: bool) -> None:
        self.window.set_running(running)

    def _sync_buttons(self) -> None:
        self.window.set_running(self._worker is not None and self._worker.isRunning())

    def shutdown(self) -> None:
        """窗口关闭时的收尾。顺序：停探测 → 停引擎 → 放依赖。

        两个 QThread 都必须收干净：运行中的 QThread 被析构会让进程直接 abort
        （不是异常，是核心转储），而探测线程（DetectWorker）是启动自检与"重新检测"
        按钮都会起的、带上限 20s/件×3 的线程，窗口关掉时它很可能还在跑。
        等不到就**故意不回收**（挂进 _ORPHANS 并由它持有引用）：宁可退出时留下一个
        线程，也不能让进程崩在关闭路径上 —— 顺便也不释放 adapter，它还在被那个线程用。
        """
        detect = self._detect_worker
        if detect is not None and detect.isRunning():
            detect.wait(3000)
            if detect.isRunning():
                _ORPHANS.append(detect)

        chat_worker = self._chat_worker
        if chat_worker is not None and chat_worker.isRunning():
            chat_worker.cancel()
            chat_worker.wait(3000)
            if chat_worker.isRunning():
                _ORPHANS.append(chat_worker)

        worker = self._worker
        if worker is not None and worker.isRunning():
            worker.cancel()
            worker.wait(8000)
            if worker.isRunning():
                _ORPHANS.append(worker)
                self._status("引擎线程未在超时内结束；已保留它以免退出时崩溃")
                return
        self._dispose_adapter()
        # 托管线程（可用模型列表这类短命线程）：等它们结束。
        # 等不到的留给模块级名单继续持有引用，最后一道闸是 workers.wait_or_exit()。
        from .workers import wait_all

        still = wait_all(3000)
        if still:
            _ORPHANS.extend(still)


class _DependencyMissing(RuntimeError):
    """环境自检没过：三件套缺一不可（规格 §9），此时**不启动**运行。"""


class _NeverConfirm:
    """占位确认端口：真正的确认由 EngineWorker 在 worker 线程里发起。"""

    def confirm(self, round_no: int, script_path: str, script: str, trusted: bool) -> bool:
        return False


def _ignore(_event: RunEvent) -> None:
    """占位 emit：worker 会用自身的 _emit 覆盖它。"""
