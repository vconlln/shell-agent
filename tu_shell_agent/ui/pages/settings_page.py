"""设置页：组件路径、默认运行根、阻断级别与超时。

只做一件事——把 `AppSettings` 读出来铺到控件上、把控件上的值写回文件；
**绝不在设置页里发起运行**，也不构造引擎对象（那些是控制器的活）。
"""

from __future__ import annotations

import sys
from typing import Any

from pathlib import Path

from PySide6.QtCore import Signal
from ..widgets.scroll import form_container, scrollable
from functools import lru_cache

from PySide6.QtGui import QFontDatabase
from PySide6.QtWidgets import (
    QComboBox,
    QDoubleSpinBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from ..settings import AppSettings, default_settings_path
from ...agent_backends import (
    BackendError,
    available_backends,
    backend_descriptor,
    resolve_command,
)

# 阻断级别的条目顺序与左栏（任务 5）逐字一致：同一件事在两个地方不能出现不同顺序
BLOCKING_LEVELS = ("error", "warning", "info", "style")
_DEFAULT_BLOCKING_LEVEL = "info"      # 与 types.RunConfig 的默认值一致

_TIMEOUT_MAX_MS = 3_600_000      # 1 小时；再长的等待不该由界面默默容忍，而是让人去改脚本

# 模型这一栏的说明分两套：这段话讲的是 opencode 的免费档陷阱（用户实际踩到过），
# 换成命令行后端之后它一个字都不适用，继续显示只会误导。
_OPENCODE_MODEL_HINT = (
    "用于生成 shell 脚本；模型对话的模型在「模型对话」面板中单独设置。"
    "留空时使用 opencode 的默认模型。opencode 未配置默认模型时将使用其免费档，"
    "而免费档仅限官方客户端调用。"
)
_CLI_MODEL_HINT = (
    "用于生成 shell 脚本，会作为 --model 传给命令行后端（例：sonnet / opus）。"
    "留空时使用该后端自己的默认模型；模型对话的模型在「模型对话」面板中单独设置。"
)


@lru_cache(maxsize=1)
def font_families() -> tuple[str, ...]:
    """系统字体族（进程内只枚举一次）。"""
    return tuple(QFontDatabase.families())


class SettingsPage(QWidget):
    """设置页的控件与读写逻辑；控件本身就是对外契约（测试与控制器按属性名取）。"""

    saved = Signal(str)          # 保存成功后的文件路径（状态栏/控制器可用）
    appearance_changed = Signal()  # 外观控件改动（缩放/字体/背景）—— 立即预览，不必等保存
    # 点「自动检测」：去 PATH（含注册表 PATH）里找三个组件的真实路径
    components_detect_requested = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("settingsPage")      # main_window 与骨架测试的契约，不要改名

        self.opencode_path_edit = QLineEdit()
        self.opencode_path_edit.setPlaceholderText("留空则从 PATH 中查找 opencode")
        self.bash_path_edit = QLineEdit()
        self.bash_path_edit.setPlaceholderText("留空则从 PATH 中查找 bash")
        self.shellcheck_path_edit = QLineEdit()
        self.shellcheck_path_edit.setPlaceholderText("留空则从 PATH 中查找 shellcheck")

        # 「自动检测」按钮：把三个组件在 PATH（含注册表 PATH）里找到的**真实路径**填进来。
        # 用户实测："windows 有环境变量，但是仍然无法自动获取" —— 光有占位文字"留空则从 PATH
        # 查找"看不出到底找到了什么，所以这里既给按钮、也在检测完成后把空栏自动填上。
        self.components_detect_button = QPushButton("自动检测")
        self.components_detect_button.setObjectName("componentsDetectButton")
        self.components_detect_button.clicked.connect(
            lambda: self.components_detect_requested.emit()
        )
        self.opencode_path_label = QLabel("opencode")
        self.components_hint = QLabel()
        self.components_hint.setObjectName("componentsHint")
        self.components_hint.setProperty("role", "muted")
        self.components_hint.setWordWrap(True)

        components = QGroupBox("组件路径")
        components_form = QFormLayout(components)
        components_form.addRow(self.opencode_path_label, self.opencode_path_edit)
        components_form.addRow("Git Bash", self.bash_path_edit)
        components_form.addRow("shellcheck", self.shellcheck_path_edit)
        components_form.addRow("", self.components_detect_button)
        components_form.addRow("", self.components_hint)

        # ── 后端 agent ───────────────────────────────────────────────
        # 用哪个 agent 生成脚本属于**运行参数**（改完保存后生效），所以这里不做即时预览；
        # 但提示行必须如实说明"当前选的是谁、命令解析成了什么、上一次检测的结果"，
        # 否则用户会以为"下拉换了就换了"，直到某次运行报出一个看不懂的错。
        self.backend_combo = QComboBox()
        self.backend_combo.setObjectName("backendCombo")
        for descriptor in available_backends():
            self.backend_combo.addItem(descriptor.display_name, descriptor.id)
        self.agent_command_edit = QLineEdit()
        self.agent_command_edit.setObjectName("agentCommandEdit")
        self.agent_command_edit.setPlaceholderText("留空则使用该后端的默认命令名称")
        self.backend_detect_button = QPushButton("检测")
        self.backend_detect_button.setObjectName("backendDetectButton")
        backend_row = QWidget()
        backend_layout = QHBoxLayout(backend_row)
        backend_layout.setContentsMargins(0, 0, 0, 0)
        backend_layout.addWidget(self.agent_command_edit, 1)
        backend_layout.addWidget(self.backend_detect_button)
        self.backend_hint = QLabel()
        self.backend_hint.setObjectName("backendHint")
        self.backend_hint.setProperty("role", "muted")
        self.backend_hint.setWordWrap(True)

        backends = QGroupBox("后端 agent")
        backends_form = QFormLayout(backends)
        backends_form.addRow("后端", self.backend_combo)
        backends_form.addRow("命令", backend_row)
        backends_form.addRow("", self.backend_hint)

        # 上一次「检测」的结论：(后端 id, 命令, 结果)。只在"与当前选择一致"时才显示 ——
        # 换了后端或改了命令之后，那条结论已经不是这一份配置的结论了。
        self._backend_probe: tuple[str, str, Any] | None = None
        self.backend_detect_button.clicked.connect(self._detect_backend)
        self.backend_combo.currentIndexChanged.connect(lambda _index: self._on_backend_changed())
        self.agent_command_edit.textChanged.connect(lambda _text: self._on_backend_changed())

        # ── 模型 ────────────────────────────────────────────────────
        # 必须显式选：opencode 自己没配默认模型时会落到免费档，而免费档只允许官方客户端，
        # 经 serve 的 API 调用会被拒（用户实际遇到的就是这条）。
        self.model_combo = QComboBox()
        self.model_combo.setObjectName("modelCombo")
        self.model_combo.setEditable(True)                 # 列表取不到时也能手输
        self.model_combo.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        self.model_combo.addItem("", "")
        self.model_combo.lineEdit().setPlaceholderText("留空 = 用该后端的默认模型")
        self.model_refresh_button = QPushButton("检测可用模型")
        self.model_refresh_button.setObjectName("modelRefreshButton")
        self.model_refresh_button.clicked.connect(self._refresh_models)
        model_row = QWidget()
        model_layout = QHBoxLayout(model_row)
        model_layout.setContentsMargins(0, 0, 0, 0)
        model_layout.addWidget(self.model_combo, 1)
        model_layout.addWidget(self.model_refresh_button)
        self.model_hint = QLabel(_OPENCODE_MODEL_HINT)
        self.model_hint.setObjectName("modelHint")
        self.model_hint.setProperty("role", "muted")
        self.model_hint.setWordWrap(True)


        self.run_root_edit = QLineEdit()
        self.run_root_edit.setPlaceholderText("每次运行的产物目录（脚本 / 输出 / meta.json）")
        self.templates_dir_edit = QLineEdit()
        self.max_rounds_spin = QSpinBox()
        self.max_rounds_spin.setRange(1, 10)           # 与左栏 run 参数一致
        self.generate_timeout_spin = QSpinBox()
        self.generate_timeout_spin.setRange(1_000, _TIMEOUT_MAX_MS)
        self.generate_timeout_spin.setSingleStep(1_000)
        self.generate_timeout_spin.setSuffix(" ms")
        self.execute_timeout_spin = QSpinBox()
        self.execute_timeout_spin.setRange(1_000, _TIMEOUT_MAX_MS)
        self.execute_timeout_spin.setSingleStep(1_000)
        self.execute_timeout_spin.setSuffix(" ms")

        run_defaults = QGroupBox("默认运行参数")
        run_form = QFormLayout(run_defaults)
        run_form.addRow("运行根目录", self.run_root_edit)
        run_form.addRow("模板目录", self.templates_dir_edit)
        run_form.addRow("最大轮次", self.max_rounds_spin)
        run_form.addRow("生成超时", self.generate_timeout_spin)
        run_form.addRow("执行超时", self.execute_timeout_spin)

        self.blocking_combo = QComboBox()
        self.blocking_combo.addItems(list(BLOCKING_LEVELS))
        self.blocking_combo.setToolTip("shellcheck 达到该级别时阻断执行并回灌自修")
        checks = QGroupBox("shellcheck")
        checks_form = QFormLayout(checks)
        checks_form.addRow("阻断级别", self.blocking_combo)

        # ── 外观 ──────────────────────────────────────────────────────
        self.ui_scale_spin = QDoubleSpinBox()
        self.ui_scale_spin.setObjectName("uiScaleSpin")
        self.ui_scale_spin.setRange(0.8, 1.6)
        self.ui_scale_spin.setSingleStep(0.05)
        self.ui_scale_spin.setDecimals(2)
        self.ui_scale_spin.setSuffix(" ×")

        self.ui_font_combo = QComboBox()
        self.ui_font_combo.setObjectName("uiFontCombo")
        self.mono_font_combo = QComboBox()
        self.mono_font_combo.setObjectName("monoFontCombo")
        # 字体列表**延迟到真正显示时才填**：本机 2100+ 个字族，两个下拉就是 4200 多次
        # addItem —— 每建一次设置页都付这个代价，实测把整套测试从 47s 拖到 332s。
        # 而设置页平时躲在工具区页签里，绝大多数时候根本不需要这份列表。
        self.ui_font_combo.addItem("系统默认", "")
        self.mono_font_combo.addItem("自动选择（等宽字体）", "")
        self._fonts_populated = False

        self.backdrop_combo = QComboBox()
        self.backdrop_combo.setObjectName("backdropCombo")
        self.backdrop_combo.addItem("不透明（默认）", "off")
        self.backdrop_combo.addItem("半透明", "translucent")
        self.backdrop_combo.addItem("亚克力模糊", "acrylic")

        # 自绘亚克力的两个旋钮：用哪张壁纸、模糊多强。留空 = 自动找当前壁纸。
        self.wallpaper_edit = QLineEdit()
        self.wallpaper_edit.setObjectName("acrylicWallpaperEdit")
        self.wallpaper_edit.setPlaceholderText("留空则自动检测系统壁纸")
        self.wallpaper_button = QPushButton("选择图片…")
        self.wallpaper_button.setObjectName("acrylicWallpaperButton")
        self.wallpaper_button.clicked.connect(self._pick_wallpaper)
        wallpaper_row = QWidget()
        wallpaper_layout = QHBoxLayout(wallpaper_row)
        wallpaper_layout.setContentsMargins(0, 0, 0, 0)
        wallpaper_layout.addWidget(self.wallpaper_edit, 1)
        wallpaper_layout.addWidget(self.wallpaper_button)

        self.acrylic_blur_spin = QSpinBox()
        self.acrylic_blur_spin.setObjectName("acrylicBlurSpin")
        self.acrylic_blur_spin.setRange(0, 120)
        self.acrylic_blur_spin.setSingleStep(4)
        self.acrylic_blur_spin.setSuffix(" px")

        self.backdrop_hint = QLabel()
        self.backdrop_hint.setObjectName("backdropHint")
        self.backdrop_hint.setProperty("role", "muted")
        self.backdrop_hint.setWordWrap(True)

        # 外观改了要**立刻看到**：用户改缩放/字体时代价最小的反馈就是马上变。
        # 保存仍然只由"保存"按钮负责落盘（预览只改内存里的绑定对象）。
        self.ui_scale_spin.valueChanged.connect(lambda _v: self.appearance_changed.emit())
        self.ui_font_combo.currentIndexChanged.connect(lambda _i: self.appearance_changed.emit())
        self.mono_font_combo.currentIndexChanged.connect(lambda _i: self.appearance_changed.emit())
        self.backdrop_combo.currentIndexChanged.connect(self._refresh_backdrop_hint)
        self.backdrop_combo.currentIndexChanged.connect(lambda _i: self.appearance_changed.emit())
        self.wallpaper_edit.textChanged.connect(lambda _t: self._refresh_backdrop_hint())
        self.wallpaper_edit.editingFinished.connect(lambda: self.appearance_changed.emit())
        self.acrylic_blur_spin.valueChanged.connect(lambda _v: self.appearance_changed.emit())

        self.save_button = QPushButton("保存")
        self.status_label = QLabel()
        self.status_label.setWordWrap(True)

        # 表单进滚动区：空间不够时滚动，而不是把控件压扁（见 widgets/scroll.py）
        content, layout = form_container()
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(scrollable(content))
        models_group = QGroupBox("脚本生成")
        models_form = QFormLayout(models_group)
        models_form.addRow("模型", model_row)
        models_form.addRow("", self.model_hint)

        layout.addWidget(components)
        layout.addWidget(backends)
        layout.addWidget(models_group)
        layout.addWidget(run_defaults)
        layout.addWidget(checks)
        # 外观
        appearance = QGroupBox("外观")
        appearance_form = QFormLayout(appearance)
        appearance_form.addRow("界面缩放", self.ui_scale_spin)
        appearance_form.addRow("界面字体", self.ui_font_combo)
        appearance_form.addRow("等宽字体（脚本/报告/输出）", self.mono_font_combo)
        appearance_form.addRow("背景效果", self.backdrop_combo)
        appearance_form.addRow("背景壁纸", wallpaper_row)
        appearance_form.addRow("模糊强度（亚克力）", self.acrylic_blur_spin)
        appearance_form.addRow("", self.backdrop_hint)
        layout.addWidget(appearance)

        layout.addWidget(self.save_button)
        layout.addWidget(self.status_label)
        layout.addStretch(1)

        self.save_button.clicked.connect(self._on_save_clicked)
        # 构造时就绑定默认位置并按磁盘上的值铺一遍：否则用户看到的是空控件，一点"保存"
        # 就把自己的设置用默认值覆盖掉了。读取是只读操作，不改文件。
        self._settings, warning = self._load_default_settings()
        self.reload()
        if warning is not None:
            self.status_label.setText(warning)      # reload() 会重写 status_label，警告要在它之后再贴

    # ---- 对外 API：控制器与测试用它换掉"当前编辑的是哪份设置" ----

    def set_settings(self, settings: AppSettings) -> None:
        """绑定要编辑的设置对象并回填控件（控制器从磁盘读出来后调用）。"""
        self._settings = settings
        self.reload()

    def apply_detected_paths(self, *, opencode: str = "", bash: str = "",
                             shellcheck: str = "") -> list[str]:
        """把探测到的组件路径填进**空着**的输入框，返回实际填了哪几项（供状态行说明）。

        两条规矩：
        - **只填空栏**：用户手填过的路径不动（那是他的选择，可能是有意指向另一个版本）；
        - 填进去的是"探测到的那一个"，也就是引擎接下来真正会用的那一个 ——
          用户能在界面上看到"自动获取"到底获取到了什么（这正是他抱怨看不到的东西）。
        """
        filled: list[str] = []
        for label, edit, path in (
            ("opencode", self.opencode_path_edit, opencode),
            ("Git Bash", self.bash_path_edit, bash),
            ("shellcheck", self.shellcheck_path_edit, shellcheck),
        ):
            found = (path or "").strip()
            if not found or edit.text().strip() or not edit.isEnabled():
                continue
            edit.setText(found)
            filled.append(f"{label} → {found}")
        if filled:
            self.components_hint.setText("已自动填入：" + "；".join(filled))
        return filled

    def reload(self) -> None:
        """把绑定对象的值铺回控件（丢弃控件上未保存的编辑）。"""
        settings = self._settings
        self.opencode_path_edit.setText(settings.opencode_path)
        self.bash_path_edit.setText(settings.bash_path)
        self.shellcheck_path_edit.setText(settings.shellcheck_path)
        self.run_root_edit.setText(settings.run_root)
        self.templates_dir_edit.setText(settings.templates_dir)
        self.max_rounds_spin.setValue(settings.max_rounds)
        self.generate_timeout_spin.setValue(settings.generate_timeout_ms)
        self.execute_timeout_spin.setValue(settings.execute_timeout_ms)
        # 级别取不到（文件被手改成别的词）就退回默认 info，而不是把下拉框设成空
        level = (
            settings.blocking_level
            if settings.blocking_level in BLOCKING_LEVELS
            else _DEFAULT_BLOCKING_LEVEL
        )
        self.blocking_combo.setCurrentText(level)
        # 外观控件也要铺回来。**必须屏蔽信号**：否则铺的过程中会触发"即时预览"，
        # 而预览的 collect() 会把还没铺完的中间态又写回设置（踩过：字体会被重置成"自动"）。
        for widget in (
            self.ui_scale_spin,
            self.backdrop_combo,
            self.wallpaper_edit,
            self.acrylic_blur_spin,
            self.model_combo,
            self.backend_combo,
            self.agent_command_edit,
        ):
            widget.blockSignals(True)
        try:
            self.ui_scale_spin.setValue(round(float(getattr(settings, "ui_scale", 1.0) or 1.0), 2))
            _select_by_data = self.backdrop_combo.findData(str(getattr(settings, "backdrop", "off")))
            self.backdrop_combo.setCurrentIndex(max(0, _select_by_data))
            self.wallpaper_edit.setText(str(getattr(settings, "acrylic_wallpaper", "") or ""))
            self.acrylic_blur_spin.setValue(int(getattr(settings, "acrylic_blur", 40) or 0))
            model = str(getattr(settings, "opencode_model", "") or "")
            if self.model_combo.findText(model) >= 0:
                self.model_combo.setCurrentIndex(self.model_combo.findText(model))
            else:
                self.model_combo.setEditText(model)
            # 后端取值先过一遍注册表：文件被手改成未知 id 时不能把下拉设成空（那是"选不中"，
            # 用户看不出到底在跑哪个后端）。AppSettings.normalize() 已经收敛过一次，
            # 这里再兜一次是因为 reload() 也可能被喂进一个没走过 load() 的对象。
            backend = str(getattr(settings, "agent_backend", "") or "")
            index = self.backend_combo.findData(backend)
            self.backend_combo.setCurrentIndex(index if index >= 0 else 0)
            self.agent_command_edit.setText(str(getattr(settings, "agent_command", "") or ""))
            # 铺控件时把上一次的检测结论丢掉：它是**另一个后端/另一条命令**的结论，
            # 留在提示行里会被读成"当前这个也能用"。
            self._backend_probe = None
        finally:
            for widget in (
                self.ui_scale_spin,
                self.backdrop_combo,
                self.wallpaper_edit,
                self.acrylic_blur_spin,
                self.model_combo,
                self.backend_combo,
                self.agent_command_edit,
            ):
                widget.blockSignals(False)
        self._refresh_backdrop_hint()
        self._refresh_backend_hint()
        self._refresh_model_hint()
        self._apply_backend_model_choices()
        self._refresh_components_hint()
        path = settings.loaded_from
        self.status_label.setText(f"保存位置：{path}" if path is not None else "尚未确定保存位置")

    def showEvent(self, event) -> None:  # noqa: N802 - Qt 命名
        """第一次显示时才把字体列表填上（见 __init__ 里的说明）。"""
        super().showEvent(event)
        if not self._fonts_populated:
            self._fonts_populated = True
            self._populate_font_combos()

    def _populate_font_combos(self) -> None:
        """填字体下拉，并**从设置里**恢复选中项。

        两个坑（都会表现为"外观设置有时生效有时不生效"，实测踩到）：
        1. 不能在填充**前**用 `combo.currentData()` 当"当前值" —— 那时下拉里只有"自动"一项，
           拿到的永远是空串，填完就把用户选的字体重置回"自动"；
        2. 填充期间顺手 `blockSignals`：在"列表里已经有自动项"的当前流程里它与不屏蔽等价
           （addItem 不改变索引，不会触发 currentIndexChanged），留着是防将来列表为空时才填充
           的情形 —— 那时会触发即时预览，而预览的 `collect()` 会把中间态写进设置。
        """
        desired = (
            str(getattr(self._settings, "ui_font", "") or ""),
            str(getattr(self._settings, "mono_font", "") or ""),
        )
        for combo, wanted in zip((self.ui_font_combo, self.mono_font_combo), desired):
            combo.blockSignals(True)
            try:
                for family in font_families():
                    combo.addItem(family, family)
                self._select_by_data(combo, wanted)
            finally:
                combo.blockSignals(False)

    @staticmethod
    def _select_by_data(combo: QComboBox, value: str) -> None:
        """按 userData 选中（字体名可能不在列表里 —— 那就是被卸载了，退回"自动"）。"""
        index = combo.findData(value)
        combo.setCurrentIndex(index if index >= 0 else 0)

    def _refresh_models(self) -> None:
        """点「检测可用模型」：起线程跑 `opencode models`，回来后填进下拉（保留当前选择）。"""
        from ..engine_worker import ModelsWorker

        if self.selected_backend_id() != "opencode":
            # 命令行后端没有"列出模型"的命令（`claude` / `codeagent` 都没有）。用户点这个按钮是
            # 想知道"我能填什么"，所以给出该后端声明的候选，并说清"值会原样传下去、也能手输" ——
            # 而不是把按钮变成一句"本后端不提供"就算了（那等于按钮没用）。
            descriptor = backend_descriptor(self.selected_backend_id())
            self._apply_backend_model_choices()
            names = "、".join(descriptor.model_suggestions) or "（该后端未声明候选）"
            self.model_hint.setText(
                f"该后端不提供模型列表。可用候选：{names}；也可直接输入完整模型名，"
                "取值会原样传给命令行后端。留空则使用其后端自己的默认模型。"
            )
            return

        self.model_refresh_button.setEnabled(False)
        self.model_hint.setText("正在检测可用模型…")
        from ..workers import track

        worker = ModelsWorker(self.opencode_path_edit.text().strip())

        def on_done(models: object) -> None:
            self._fill_models([str(item) for item in (models or [])])
            self.model_refresh_button.setEnabled(True)
            self.model_hint.setText(f"检测到 {len(models or [])} 个模型，请选择一个已配置凭据的。")

        def on_failed(message: str) -> None:
            self.model_refresh_button.setEnabled(True)
            self.model_hint.setText(f"检测失败：{message}")

        worker.done.connect(on_done)
        worker.failed.connect(on_failed)
        track(worker)      # 托管：引用被覆盖 / 退出时还在跑都会让 Qt abort（见 ui/workers.py）

    def _fill_models(self, models: list[str]) -> None:
        """把模型列表铺进下拉，**保留用户当前输入**（列表刷新不该把已选的模型弄丢）。"""
        current = self.model_combo.currentText().strip()
        self.model_combo.blockSignals(True)
        try:
            self.model_combo.clear()
            self.model_combo.addItem("", "")
            for model in models:
                self.model_combo.addItem(model, model)
            index = self.model_combo.findText(current)
            if index >= 0:
                self.model_combo.setCurrentIndex(index)
            else:
                self.model_combo.setEditText(current)
        finally:
            self.model_combo.blockSignals(False)

    # ---- 后端 agent ----

    def selected_backend_id(self) -> str:
        """当前下拉选中的后端 id（控件上的值，未必已经保存）。"""
        return str(self.backend_combo.currentData() or "")

    def effective_command(self) -> str:
        """当前选择下**实际会执行**的命令。

        与控制器走同一个 `resolve_command`：界面提示行说的命令与真正跑的命令必须是同一个，
        否则这个提示行就是在骗人（"显示 claude、实际跑 opencode"这种故障最难查）。
        """
        try:
            return resolve_command(
                self.selected_backend_id(),
                self.agent_command_edit.text(),
                fallback=str(getattr(self._settings, "opencode_path", "") or ""),
            )
        except BackendError:
            return ""

    def _apply_backend_model_choices(self) -> None:
        """按当前后端刷新模型候选与说明。

        两种后端的模型来源不同：opencode 能**列出**可用模型（`opencode models`），
        命令行后端没有这个能力 —— 那就给一组常用别名当候选、并允许直接输入。
        无论哪种，值都会被原样传给后端（opencode 走请求体，命令行走 `--model`）。
        """
        try:
            descriptor = backend_descriptor(self.selected_backend_id())
        except Exception:  # noqa: BLE001 - 未知后端由下拉保证不会出现，这里只做兜底
            return
        current = self.model_combo.currentText().strip()
        self.model_combo.blockSignals(True)
        try:
            self.model_combo.clear()
            self.model_combo.addItem("", "")
            for name in descriptor.model_suggestions:
                self.model_combo.addItem(name, name)
            index = self.model_combo.findText(current)
            if index >= 0:
                self.model_combo.setCurrentIndex(index)
            else:
                self.model_combo.setEditText(current)
        finally:
            self.model_combo.blockSignals(False)
        self.model_hint.setText(descriptor.model_hint)

    def _on_backend_changed(self) -> None:
        """下拉或命令框变了：丢掉旧结论，重铺提示行（保存后才真正生效）。"""
        self._backend_probe = None
        self._apply_backend_model_choices()
        self._refresh_components_hint()
        self._refresh_backend_hint()
        self._refresh_model_hint()

    def _refresh_model_hint(self) -> None:
        """模型这一栏的说明跟着后端走（opencode 的免费档提示对命令行后端不适用）。

        说明语由**后端自己的文件**声明（`model_hint`）；没声明才退回两个通用常量 ——
        这样加一个新后端时，说明不会漏写、也不用改这个文件。
        """
        try:
            descriptor = backend_descriptor(self.selected_backend_id())
        except BackendError:
            self.model_hint.setText(_OPENCODE_MODEL_HINT)
            return
        self.model_hint.setText(
            descriptor.model_hint or (_OPENCODE_MODEL_HINT if descriptor.needs_serve else _CLI_MODEL_HINT)
        )

    def _refresh_components_hint(self) -> None:
        """`opencode` 路径只在"后端 = opencode"时有意义，其余后端要把它标出来并置灰。

        用户问过："组件路径也还是 opencode，那我要是选择 codeagent 呢？" —— 答案是
        agent 命令在「后端 agent」里设置；bash 与 shellcheck 任何后端都要用（引擎执行脚本
        的是它们，换 agent 不换执行者）。界面必须把这件事说清楚，而不是留一个人人误会的输入框。
        """
        is_opencode = self.selected_backend_id() == "opencode"
        self.opencode_path_edit.setEnabled(is_opencode)
        self.opencode_path_label.setText("opencode" if is_opencode else "opencode（仅该后端使用）")
        self.components_hint.setText(
            "Git Bash 与 shellcheck 由引擎执行脚本时使用，任何后端都需要。"
            if not is_opencode
            else "当前后端是 opencode，上面的路径生效。"
        )
        if not is_opencode:
            self.components_hint.setText(
                "opencode 路径仅在后端为 opencode 时使用；当前后端是 "
                f"{self.backend_combo.currentText()}，其命令在上面的「后端 agent」中设置。"
                "Git Bash 与 shellcheck 由引擎执行脚本时使用，任何后端都需要。"
            )

    def _refresh_backend_hint(self) -> None:
        """把"当前后端 / 解析出的命令 / 上一次检测的结论"如实铺在提示行里。"""
        try:
            descriptor = backend_descriptor(self.selected_backend_id())
        except BackendError as error:
            self.backend_hint.setText(str(error))
            return
        command = self.effective_command()
        lines = [
            f"当前后端：{descriptor.display_name}。{descriptor.summary}",
            f"命令：{command}。" if command else "命令尚未填写，运行时会无法启动该后端。",
        ]
        if descriptor.needs_serve and not self.agent_command_edit.text().strip():
            lines.append("命令留空时使用「组件路径」中的 opencode 路径。")
        probe = self._backend_probe
        if probe is not None and probe[0] == descriptor.id and probe[1] == command:
            result = probe[2]
            lines.append(result.detail())
        else:
            lines.append("按「检测」运行它的版本命令，确认该命令在这台机器上可用。")

        # **未保存**必须说出来：用户实测"选了 codeagent，对话里还是检测不了模型"——
        # 因为他只改了这个下拉、没按保存，而运行与取模型都按**已保存**的后端走
        # （下一行会显示"当前实际使用"）。不说清楚的话，界面看着像已经切过去了。
        saved = str(getattr(self._settings, "agent_backend", "") or "")
        if saved and saved != descriptor.id:
            try:
                saved_name = backend_descriptor(saved).display_name
            except BackendError:
                saved_name = saved
            lines.append(
                f"⚠ 尚未保存：下面这些改动还不生效，当前实际使用的是「{saved_name}」。"
                "点「保存」之后才会切换。"
            )
        self.backend_hint.setText("\n".join(lines))

    def _detect_backend(self) -> None:
        """点「检测」：在 worker 线程里跑该后端的版本命令（子进程不能起在界面线程上）。"""
        from ..engine_worker import BackendProbeWorker
        from ..workers import track

        backend_id = self.selected_backend_id()
        command = self.agent_command_edit.text().strip()
        self.backend_detect_button.setEnabled(False)
        self.backend_hint.setText(f"正在检测 {backend_id}…")
        worker = BackendProbeWorker(backend_id, command)

        def on_done(result: object) -> None:
            self._backend_probe = (backend_id, self.effective_command(), result)
            self.backend_detect_button.setEnabled(True)
            self._refresh_backend_hint()

        def on_failed(message: str) -> None:
            self.backend_detect_button.setEnabled(True)
            self.backend_hint.setText(f"检测失败：{message}")

        worker.done.connect(on_done)
        worker.failed.connect(on_failed)
        track(worker)      # 托管：引用被覆盖 / 退出时还在跑都会让 Qt abort（见 ui/workers.py）

    def _pick_wallpaper(self) -> None:
        """选一张壁纸图片。只在点击时弹对话框 —— 无头测试不会走到这里。"""
        from PySide6.QtWidgets import QFileDialog

        from .. import acrylic as acrylic_module

        chosen, _ = QFileDialog.getOpenFileName(
            self, "选择壁纸图片", self.wallpaper_edit.text().strip() or str(Path.home()),
            "图片 (" + " ".join(f"*{suffix}" for suffix in acrylic_module.IMAGE_SUFFIXES) + ")",
        )
        if chosen:
            self.wallpaper_edit.setText(chosen)
            self.appearance_changed.emit()

    def _refresh_backdrop_hint(self) -> None:
        """如实说明模糊能不能用：拿不到就别让用户以为开了。"""
        from .. import acrylic as acrylic_module

        mode = self.backdrop_combo.currentData()
        if mode == "off":
            self.backdrop_hint.setText("")
            return
        found = acrylic_module.find_wallpaper(self.wallpaper_edit.text().strip())
        detail = (
            "亚克力模糊：壁纸高斯模糊后再压深色。模糊强度只对此模式生效。"
            if mode == "acrylic"
            else "半透明：壁纸不模糊，只压一层淡色。"
        )
        self.backdrop_hint.setText(
            detail
            + "两种模式都由界面自绘底色，不依赖系统合成器或窗口透明。"
            + (f"当前壁纸：{Path(found).name}" if found else "未检测到壁纸图片，将使用纯色背景。")
        )

    def collect(self) -> AppSettings:
        """控件 → `AppSettings`（**不落盘**）。直接写回绑定对象，免得丢掉 load() 记住的路径。"""
        settings = self._settings
        settings.opencode_path = self.opencode_path_edit.text().strip()
        settings.bash_path = self.bash_path_edit.text().strip()
        settings.shellcheck_path = self.shellcheck_path_edit.text().strip()
        settings.run_root = self.run_root_edit.text().strip()
        settings.templates_dir = self.templates_dir_edit.text().strip()
        settings.max_rounds = self.max_rounds_spin.value()
        settings.generate_timeout_ms = self.generate_timeout_spin.value()
        settings.execute_timeout_ms = self.execute_timeout_spin.value()
        settings.blocking_level = self.blocking_combo.currentText()
        settings.ui_scale = round(float(self.ui_scale_spin.value()), 2)
        settings.ui_font = str(self.ui_font_combo.currentData() or "")
        settings.mono_font = str(self.mono_font_combo.currentData() or "")
        settings.backdrop = str(self.backdrop_combo.currentData() or "off")
        settings.acrylic_wallpaper = self.wallpaper_edit.text().strip()
        settings.acrylic_blur = int(self.acrylic_blur_spin.value())
        settings.opencode_model = self.model_combo.currentText().strip()
        settings.agent_backend = self.selected_backend_id()
        settings.agent_command = self.agent_command_edit.text().strip()
        return settings

    def save(self) -> Path | None:
        """控件 → 绑定对象 → 写回它 load() 时记住的文件；失败只报错不抛（界面不能因写盘失败炸掉）。"""
        settings = self.collect()
        try:
            settings.save()
        except (OSError, ValueError) as exc:
            self.status_label.setText(f"保存失败：{exc}")
            return None
        path = settings.loaded_from
        self.status_label.setText(f"已保存：{path}")
        if path is not None:
            self.saved.emit(str(path))
        return path

    # ---- 内部 ----

    def _load_default_settings(self) -> tuple[AppSettings, str | None]:
        """默认位置的设置；文件坏了就当默认值用，但要如实告诉用户（否则界面直接起不来）。

        坏文件这条路径刻意**不**自动重写：用户手改坏的 JSON 里可能有他想要的内容，
        等他看到提示、自己决定点不点"保存"，再由那次保存覆盖——界面不替用户删数据。
        """
        path = default_settings_path()
        try:
            return AppSettings.load(path), None
        except (OSError, ValueError) as exc:      # 含 json.JSONDecodeError（ValueError 子类）
            # 内容坏了与读不动（权限/占用）要分开说：后者保存同样会失败，
            # 提示"保存会覆盖它"会把用户引到错误的方向。
            if isinstance(exc, ValueError):
                warning = f"设置文件内容无法解析（{exc}），当前显示默认值；保存会覆盖它"
            else:
                warning = f"设置文件无法读取（{exc}），当前显示默认值；修好之前保存也会失败"
            return AppSettings.defaults_for(path), warning

    def _on_save_clicked(self) -> None:
        # clicked 带一个 bool 实参，转一道手免得被它噎住
        self.save()
