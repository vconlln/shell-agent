"""设置页：组件路径、默认运行根、阻断级别与超时。

只做一件事——把 `AppSettings` 读出来铺到控件上、把控件上的值写回文件；
**绝不在设置页里发起运行**，也不构造引擎对象（那些是控制器的活）。
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Signal
from ..widgets.scroll import form_container, scrollable
from PySide6.QtWidgets import (
    QComboBox,
    QFormLayout,
    QGroupBox,
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


class SettingsPage(QWidget):
    """设置页的控件与读写逻辑；控件本身就是对外契约（测试与控制器按属性名取）。"""

    saved = Signal(str)          # 保存成功后的文件路径（状态栏/控制器可用）

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
        path = settings.loaded_from
        self.status_label.setText(f"保存位置：{path}" if path is not None else "尚未确定保存位置")

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
