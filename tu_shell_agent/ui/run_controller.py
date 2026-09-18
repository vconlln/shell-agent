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
from ..run_store.layout import make_run_id, run_dir_for
from ..run_store.store import RunStore
from ..template_store.builtins import BUILTIN_TEMPLATES
from ..template_store.render import PlaceholderSpec, declared_names, render_template
from ..types import (
    DetectionReport,
    ExecuteEvidence,
    FailureEvidence,
    RunConfig,
    RunEvent,
)
from .engine_worker import DetectWorker, EngineWorker
from .widgets.confirm_dialog import ConfirmDialog

# 引擎写 notes.md 时用的分隔（见 orchestrator.loop 的 _check_script 调用点）。
_NOTES_SEPARATOR = "\n\n## 假设\n"


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
        self._run_dir = ""
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
        window.set_running(False)

    # ── 环境自检 ──────────────────────────────────────────────────
    def recheck_environment(self) -> None:
        """重跑三件套探测并铺到自检页（规格 §9：缺一不可）。

        探测起子进程，所以走线程；结果只做展示 —— 真正的"能不能跑"由每次运行前的
        `_ensure_deps()` 现场再判一次（用户可能在自检之后把工具挪走）。
        """
        if self._detect_worker is not None and self._detect_worker.isRunning():
            return
        worker = DetectWorker(self._path_overrides())
        worker.done.connect(self._on_detected)
        worker.failed.connect(lambda message: self._status(f"环境探测失败：{message}"))
        self._detect_worker = worker
        self._status("正在检测环境…")
        worker.start()

    def _on_detected(self, report: DetectionReport) -> None:
        self.window.selfcheck_page.render(report)
        if report.problems:
            self._status(f"环境自检有 {len(report.problems)} 个问题（见「环境自检」页）")
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
            self._status("没有可续跑的会话（meta.json 里没有 sessionId）")
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

        self._run(entry, run_dir, config)

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

        self._run(entry, run_dir, config)

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
        """返回 (opencode, toolchain)。注入过替身就直接用；否则现场探测三件套。"""
        if self._opencode is not None and self._toolchain is not None:
            return self._opencode, self._toolchain

        from ..opencode_adapter import OpencodeAdapter
        from ..shell_toolchain.detect import detect_all, system_deps
        from ..shell_toolchain.facade import ShellToolchain

        overrides = self._path_overrides()
        report = detect_all(system_deps(overrides))
        if report.problems:
            raise _DependencyMissing("环境自检未通过：" + "；".join(report.problems))
        assert report.opencode and report.bash and report.shellcheck
        self._adapter = OpencodeAdapter(
            opencode_path=report.opencode.path, note=self._status
        )
        self._opencode = self._adapter
        # 三个 path 一起进 facade：run_loop 内部还会再 detect 一次，少了 override 会在
        # "shellcheck 不在 PATH"的机器上把已解析出的路径又判成缺失 → aborted_dependency。
        self._toolchain = ShellToolchain(report.bash.path, report.shellcheck.path, overrides)
        return self._opencode, self._toolchain

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
        if config.run_root:
            return config
        # 左栏没填就退到构造参数给的运行根（"改后重跑"常常没走左栏的校验）。
        # 用 replace 而不是 `RunConfig(**config.__dict__)`：RunConfig 是 slots 数据类，没有 __dict__。
        return replace(config, run_root=self._run_root)

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
            self._status(f"第 {event.round} 轮 · {payload.get('phase')}")
        elif event.type == "assistant_delta":
            self._status(f"第 {event.round} 轮 · 模型输出中…")
        elif event.type == "script":
            script = payload.get("script") or ""
            center.show_round(event.round, script)
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
            return bool(self.confirm_answer)
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
        self._set_running(False)
        self._status(f"结论：{result.outcome}（{result.rounds} 轮）")
        self._dispose_adapter()
        self.window.history_page.reload()
        self._sync_buttons()
        # 最后才发：调用方（测试、将来的批处理）收到这个信号时，界面与按钮状态必须已经收好。
        self.finished.emit(result)

    def _on_failed(self, message: str) -> None:
        """引擎层异常：没有 LoopResult 可给，所以走 failed 而不是 finished。

        两个信号**二选一**（每次运行恰好一个）。等待结论的调用方要同时听两个，
        否则引擎异常时它会一直等不到——这正是加这个信号的原因。
        """
        self._set_running(False)
        self._status(f"引擎异常：{message}")
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
        center = self.window.center_pane
        right = self.window.right_pane
        # 回放前必须先清空：否则上一次运行的轮次会被当成"这一轮的上一轮"，对比页会给出
        # 两次**不同运行**之间的假差异，时间线里也会留着别人的轮次。
        center.reset()
        right.reset()
        if script:
            center.show_round(int(meta.get("rounds") or 1), script)
        findings = snapshot.get("findings")
        if findings is not None:
            # None 表示"这份运行没留下 shellcheck 报告"（例如第 1 轮契约失败就退出了）：
            # 不能喂空元组，那会被右栏渲染成"报告：0 处（本轮没有发现）"——把"没有数据"
            # 说成"检查过了没问题"，是这份界面里最不该出现的假结论。
            right.render_findings(findings)
        notes, assumptions = _split_notes(snapshot.get("notes") or "")
        right.render_notes(notes, assumptions)
        right.output_view.setPlainText(
            (snapshot.get("stdout") or "") + (snapshot.get("stderr") or "")
        )
        self._status(f"回放：{meta.get('outcome') or '未知'}（{meta.get('rounds', '?')} 轮）")

    # ── 小工具 ────────────────────────────────────────────────────
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
        """窗口关闭时的收尾：取消正在跑的运行并等它退出，再释放 adapter。"""
        worker = self._worker
        if worker is not None and worker.isRunning():
            worker.cancel()
            worker.wait(5000)
        self._dispose_adapter()


class _DependencyMissing(RuntimeError):
    """环境自检没过：三件套缺一不可（规格 §9），此时**不启动**运行。"""


class _NeverConfirm:
    """占位确认端口：真正的确认由 EngineWorker 在 worker 线程里发起。"""

    def confirm(self, round_no: int, script_path: str, script: str, trusted: bool) -> bool:
        return False


def _ignore(_event: RunEvent) -> None:
    """占位 emit：worker 会用自身的 _emit 覆盖它。"""
