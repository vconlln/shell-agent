"""设置页：组件路径、默认运行根、阻断级别与超时。

只做一件事——把 `AppSettings` 读出来铺到控件上、把控件上的值写回文件；
**绝不在设置页里发起运行**，也不构造引擎对象（那些是控制器的活）。
"""

from __future__ import annotations

import sys

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

# 阻断级别的条目顺序与左栏（任务 5）逐字一致：同一件事在两个地方不能出现不同顺序
BLOCKING_LEVELS = ("error", "warning", "info", "style")
_DEFAULT_BLOCKING_LEVEL = "info"      # 与 types.RunConfig 的默认值一致

_TIMEOUT_MAX_MS = 3_600_000      # 1 小时；再长的等待不该由界面默默容忍，而是让人去改脚本


@lru_cache(maxsize=1)
def font_families() -> tuple[str, ...]:
    """系统字体族（进程内只枚举一次）。"""
    return tuple(QFontDatabase.families())


class SettingsPage(QWidget):
    """设置页的控件与读写逻辑；控件本身就是对外契约（测试与控制器按属性名取）。"""

    saved = Signal(str)          # 保存成功后的文件路径（状态栏/控制器可用）
    appearance_changed = Signal()  # 外观控件改动（缩放/字体/背景）—— 立即预览，不必等保存

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("settingsPage")      # main_window 与骨架测试的契约，不要改名

        self.opencode_path_edit = QLineEdit()
        self.opencode_path_edit.setPlaceholderText("留空 = 用 PATH 里找到的 opencode")
        self.bash_path_edit = QLineEdit()
        self.bash_path_edit.setPlaceholderText("留空 = 用 PATH 里找到的 bash")
        self.shellcheck_path_edit = QLineEdit()
        self.shellcheck_path_edit.setPlaceholderText("留空 = 用 PATH 里找到的 shellcheck")

        components = QGroupBox("组件路径")
        components_form = QFormLayout(components)
        components_form.addRow("opencode", self.opencode_path_edit)
        components_form.addRow("Git Bash", self.bash_path_edit)
        components_form.addRow("shellcheck", self.shellcheck_path_edit)

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
        self.blocking_combo.setToolTip("shellcheck 发现达到该级别就阻断执行并回灌自修")
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
        self.mono_font_combo.addItem("自动（挑一个可用的等宽字体）", "")
        self._fonts_populated = False

        self.backdrop_combo = QComboBox()
        self.backdrop_combo.setObjectName("backdropCombo")
        self.backdrop_combo.addItem("不透明（默认）", "off")
        self.backdrop_combo.addItem("半透明", "translucent")
        self.backdrop_combo.addItem("亚克力模糊（问系统要，Windows 有）", "blur")
        self.backdrop_combo.addItem("亚克力模糊（自绘壁纸，不需要系统支持）", "acrylic")

        # 自绘亚克力的两个旋钮：用哪张壁纸、模糊多强。留空 = 自动找当前壁纸。
        self.wallpaper_edit = QLineEdit()
        self.wallpaper_edit.setObjectName("acrylicWallpaperEdit")
        self.wallpaper_edit.setPlaceholderText("留空 = 自动找当前壁纸（DMS / KDE / hyprpaper）")
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
        layout.addWidget(components)
        layout.addWidget(run_defaults)
        layout.addWidget(checks)
        # 外观
        appearance = QGroupBox("外观")
        appearance_form = QFormLayout(appearance)
        appearance_form.addRow("界面缩放", self.ui_scale_spin)
        appearance_form.addRow("界面字体", self.ui_font_combo)
        appearance_form.addRow("等宽字体（脚本/报告/输出）", self.mono_font_combo)
        appearance_form.addRow("背景效果", self.backdrop_combo)
        appearance_form.addRow("亚克力壁纸", wallpaper_row)
        appearance_form.addRow("模糊强度", self.acrylic_blur_spin)
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
        ):
            widget.blockSignals(True)
        try:
            self.ui_scale_spin.setValue(round(float(getattr(settings, "ui_scale", 1.0) or 1.0), 2))
            _select_by_data = self.backdrop_combo.findData(str(getattr(settings, "backdrop", "off")))
            self.backdrop_combo.setCurrentIndex(max(0, _select_by_data))
            self.wallpaper_edit.setText(str(getattr(settings, "acrylic_wallpaper", "") or ""))
            self.acrylic_blur_spin.setValue(int(getattr(settings, "acrylic_blur", 40) or 0))
        finally:
            for widget in (
                self.ui_scale_spin,
                self.backdrop_combo,
                self.wallpaper_edit,
                self.acrylic_blur_spin,
            ):
                widget.blockSignals(False)
        self._refresh_backdrop_hint()
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
        if mode == "acrylic":
            # 自绘：不问系统，只问"壁纸找不找得到"—— 找到就能糊，找不到就如实说
            found = acrylic_module.find_wallpaper(self.wallpaper_edit.text().strip())
            self.backdrop_hint.setText(
                f"自绘模糊（不需要系统支持）：{Path(found).name}" if found
                else "自绘模糊：没找到壁纸图片，请在上面指定一张（否则只有半透明）"
            )
            return
        if mode != "blur":
            self.backdrop_hint.setText("")
            return
        self.backdrop_hint.setText(
            "模糊由窗口管理器提供：" + ("当前平台看起来支持（保存后生效）"
            if sys.platform == "win32" else "当前桌面多半不支持，保存后会退化为半透明（不模糊）")
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
