"""模板库面板：列表 + 正文编辑 + 占位符表单 + trusted + 渲染预览。

面板只负责把用户的选择翻译成 `LoopInput` 需要的两样东西（`TemplateSpec` 与
占位符取值），**不执行任何脚本**：执行与校验都在 run_controller 里。

两个刻意的界面决定：
1. 列表项文字就是模板 id，而不是 `meta.name`。id 是运行参数的唯一键，界面
   显示什么就该能直接拿去用；可读的名称放进 tooltip，避免"看到的"和"选中的"
   对不上。测试契约同样按下标取 list_widget 的文字。
2. 渲染失败（正文里有未声明的占位符）把错误显示在预览区，不弹窗。用户正在
   编辑正文，中途出现未声明占位符是常态，弹窗会打断输入。
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from ...orchestrator.contract import extract_anchors
from ...orchestrator.loop import TemplateSpec
from ...template_store.render import PlaceholderSpec, declared_names, render_template, to_lf
from ...template_store.store import TemplateInput, TemplateStore

# 与 CLI 的 --templates-dir 默认值保持一致：同一条命令行的两种入口（CLI/界面）
# 默认看到同一份模板库，否则「命令行跑得好好的、界面里却没有这个模板」。
DEFAULT_TEMPLATES_DIR = ".tu-templates"

# 模板 id 的合法字符（与 TemplateStore._ID_OK 一致），导入时用它清洗文件名。
_ID_ALLOWED = set("abcdefghijklmnopqrstuvwxyz0123456789-")


def _template_id_from_path(path: Path) -> str:
    """把拖进来的文件名转成合法 id：小写、非法字符换 `-`、去掉 `.tpl.sh` 后缀。"""
    stem = path.name
    if stem.endswith(".tpl.sh"):
        stem = stem[: -len(".tpl.sh")]
    cleaned = "".join(ch if ch in _ID_ALLOWED else "-" for ch in stem.lower())
    return cleaned.strip("-") or "imported"


class TemplatesPane(QWidget):
    """模板库：列表 + 正文编辑 + 占位符表单 + trusted + 渲染预览。"""

    template_changed = Signal(str)  # 选中/保存后发出模板 id

    def __init__(self, store: TemplateStore | None = None, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("templatesPane")

        # store 可省：main_window 用无参构造（真实模板库），测试注入临时目录。
        self._store = store if store is not None else TemplateStore(DEFAULT_TEMPLATES_DIR)
        self._current_id: str | None = None
        # 表单控件按占位符名索引（objectName 也带名字），set_placeholder_value 与
        # 保存时都要靠它反查，不能再依赖 QFormLayout 的 item 交替顺序。
        self._placeholder_inputs: dict[str, QLineEdit] = {}

        self._build_ui()
        self._connect_signals()

    # ── 装配 ────────────────────────────────────────────────────────────
    def _build_ui(self) -> None:
        self.list_widget = QListWidget()
        self.list_widget.setObjectName("templatesList")

        self.body_edit = QPlainTextEdit()
        self.body_edit.setObjectName("templateBody")
        self.body_edit.setPlaceholderText("模板正文；锚点 # @@TU:BODY@@ 生成时必须保留")

        self.placeholder_form = QFormLayout()
        placeholder_host = QWidget()
        placeholder_host.setLayout(self.placeholder_form)
        # 占位符个数由模板正文决定，套滚动区免得把正文编辑区挤没。
        placeholder_scroll = QScrollArea()
        placeholder_scroll.setObjectName("placeholderArea")
        placeholder_scroll.setWidgetResizable(True)
        placeholder_scroll.setWidget(placeholder_host)

        self.trusted_check = QCheckBox("trusted（本次运行跳过执行确认）")
        self.trusted_check.setObjectName("trustedCheck")

        self.preview = QPlainTextEdit()
        self.preview.setObjectName("templatePreview")
        self.preview.setReadOnly(True)
        self.preview.setPlaceholderText("渲染预览（未声明的占位符会在这里报错）")

        self.save_button = QPushButton("保存")
        self.delete_button = QPushButton("删除")
        self.import_button = QPushButton("导入 .tpl.sh")
        self.new_button = QPushButton("新建")
        buttons = QHBoxLayout()
        for button in (self.save_button, self.delete_button, self.import_button, self.new_button):
            buttons.addWidget(button)
        buttons.addStretch(1)

        editor = QWidget()
        editor_layout = QVBoxLayout(editor)
        editor_layout.addWidget(QLabel("正文"))
        editor_layout.addWidget(self.body_edit, 3)
        editor_layout.addWidget(QLabel("占位符"))
        editor_layout.addWidget(placeholder_scroll, 2)
        editor_layout.addWidget(self.trusted_check)
        editor_layout.addWidget(QLabel("渲染预览"))
        editor_layout.addWidget(self.preview, 3)
        editor_layout.addLayout(buttons)

        splitter = QSplitter(Qt.Orientation.Vertical)
        splitter.addWidget(self.list_widget)
        splitter.addWidget(editor)
        splitter.setSizes([120, 500])

        layout = QVBoxLayout(self)
        layout.addWidget(splitter, 1)

    def _connect_signals(self) -> None:
        self.list_widget.currentItemChanged.connect(self._on_current_item_changed)
        self.trusted_check.toggled.connect(self._refresh_preview)
        self.save_button.clicked.connect(self.save_current)
        self.delete_button.clicked.connect(self.delete_current)
        self.import_button.clicked.connect(self.import_from_file)
        self.new_button.clicked.connect(self.create_current)
        # 正文改动会新增/删除占位符，表单与预览都必须跟着走。
        self.body_edit.textChanged.connect(self._on_body_changed)

    # ── 列表与选择 ──────────────────────────────────────────────────────
    def set_store(self, store: TemplateStore) -> None:
        """换一个模板库（设置页里的"模板目录"靠它生效）。

        换库后必须重载列表并把当前选中清掉：留着旧库的 `_current_id` 会让人以为
        选中的还是原来那个模板，而正文已经来自另一个库（甚至根本不存在）。
        """
        self._store = store
        self._current_id = None
        self.reload()

    def reload(self) -> None:
        """重新读模板库并尽量保持原选中项（保存/删除后都会调用）。"""
        keep = self._current_id
        self.list_widget.blockSignals(True)
        self.list_widget.clear()
        for meta in self._store.list():
            item = QListWidgetItem(meta.id)
            item.setToolTip(meta.name if meta.name != meta.id else meta.id)
            self.list_widget.addItem(item)
        self.list_widget.blockSignals(False)

        target = self._row_for(keep) if keep is not None else -1
        if target < 0 and self.list_widget.count():
            target = 0
        if target < 0:
            self._current_id = None
            self.body_edit.clear()
            self._rebuild_placeholder_form([])
            self._refresh_preview()
            return
        # setCurrentRow 会经 currentItemChanged 走到 select()，这里不重复载入。
        self.list_widget.setCurrentRow(target)

    def select(self, template_id: str) -> None:
        """载入模板正文；占位符表单按正文里真正出现的名字生成。"""
        meta = self._store.get(template_id)
        body = to_lf(self._store.read(template_id))
        self._current_id = template_id

        row = self._row_for(template_id)
        if row >= 0 and self.list_widget.currentRow() != row:
            self.list_widget.blockSignals(True)   # 程序化选中不该再触发一次 select
            self.list_widget.setCurrentRow(row)
            self.list_widget.blockSignals(False)

        self.body_edit.blockSignals(True)         # 回填正文不是用户编辑，别重建表单
        self.body_edit.setPlainText(body)
        self.body_edit.blockSignals(False)

        self.trusted_check.blockSignals(True)
        self.trusted_check.setChecked(meta.trusted)
        self.trusted_check.blockSignals(False)

        self._rebuild_placeholder_form(self._declared_specs(body, meta.placeholders))
        self._refresh_preview()
        self.template_changed.emit(template_id)

    def _row_for(self, template_id: str | None) -> int:
        if template_id is None:
            return -1
        for row in range(self.list_widget.count()):
            if self.list_widget.item(row).text() == template_id:
                return row
        return -1

    def _on_current_item_changed(self, current: QListWidgetItem | None, _previous) -> None:
        if current is not None:
            self.select(current.text())

    # ── 占位符 ──────────────────────────────────────────────────────────
    def _declared_specs(
        self, body: str, declared: list[PlaceholderSpec]
    ) -> list[PlaceholderSpec]:
        """以正文为准合并元数据：正文里出现的名字一个都不能少。

        模板文件可能被用户在编辑器里手改过（新增了占位符而 metadata 没更新），
        少建一个输入框就会让渲染永远报"未声明"；反过来元数据里有、正文里已经没有
        的占位符则保留，免得用户只是临时注释掉一行就丢掉默认值。
        """
        by_name = {spec.name: spec for spec in declared}
        merged: list[PlaceholderSpec] = []
        for name in declared_names(body):
            merged.append(by_name.get(name, PlaceholderSpec(name=name)))
        seen = {spec.name for spec in merged}
        merged.extend(spec for spec in declared if spec.name not in seen)
        return merged

    def _rebuild_placeholder_form(self, specs: list[PlaceholderSpec]) -> None:
        previous = self.placeholder_values()
        while self.placeholder_form.rowCount():
            self.placeholder_form.removeRow(0)     # removeRow 会连同控件一起销毁
        self._placeholder_inputs.clear()

        for spec in specs:
            field = QLineEdit()
            # objectName 必须含占位符名：测试与外部代码据此查找控件。
            field.setObjectName(f"placeholder_{spec.name}")
            field.setText(previous.get(spec.name, spec.default or ""))
            if spec.description:
                field.setToolTip(spec.description)
            self.placeholder_form.addRow(spec.name, field)
            self._placeholder_inputs[spec.name] = field
            field.textChanged.connect(self._refresh_preview)

    def placeholder_input(self, name: str) -> QLineEdit | None:
        return self._placeholder_inputs.get(name)

    def set_placeholder_value(self, name: str, value: str) -> None:
        field = self._placeholder_inputs.get(name)
        if field is None:
            raise KeyError(f"当前模板没有占位符：{name}")
        field.setText(value)                       # textChanged 会顺带刷新预览

    def placeholder_values(self) -> dict[str, str]:
        """用户**真正填过**的占位符值；空输入框不出现在结果里。

        这一条是必须的：`render_template` 里 values 的优先级高于模板元数据的默认值
        （render.py 的 `if name in values: return values[name]`）。而输入框一建出来
        就是空的，若把空串当成"用户给的值"，选中内置模板后什么都不填就会把默认值顶掉 ——
        `{{work_dir:.}}` 渲染成 `DIR=""`、`{{script_name:task.sh}}` 渲染成空名字，
        于是预览里是一份会跑失败的骨架，而这份骨架正是交给模型生成的基准。
        空 = 没填，让默认值（或占位符自带的行内默认）生效。
        """
        return {
            name: field.text()
            for name, field in self._placeholder_inputs.items()
            if field.text().strip()
        }

    # ── 预览 ────────────────────────────────────────────────────────────
    def _on_body_changed(self) -> None:
        specs = self._declared_specs(self.body_edit.toPlainText(), self._current_specs())
        self._rebuild_placeholder_form(specs)
        self._refresh_preview()

    def _current_specs(self) -> list[PlaceholderSpec]:
        if self._current_id is None:
            return []
        try:
            return list(self._store.get(self._current_id).placeholders)
        except KeyError:
            return []

    def _refresh_preview(self) -> None:
        """渲染预览；失败只在预览区显示原因，不弹窗、不阻断编辑。"""
        try:
            self.preview.setPlainText(self.rendered_skeleton())
        except ValueError as exc:
            self.preview.setPlainText(f"无法渲染：{exc}")

    def rendered_skeleton(self) -> str:
        """当前正文 + 当前取值渲染后的骨架（未声明的占位符会抛 ValueError）。"""
        body = to_lf(self.body_edit.toPlainText())
        return render_template(body, self._declared_specs(body, self._current_specs()),
                               self.placeholder_values())

    # ── 保存 / 删除 / 导入 ──────────────────────────────────────────────
    def save_current(self) -> None:
        """写回正文、占位符元数据与 trusted。正文经 LF 归一化，避免 CRLF 进 shellcheck。"""
        if self._current_id is None:
            return
        template_id = self._current_id
        body = to_lf(self.body_edit.toPlainText())
        # 占位符元数据以正文为准重算，但 description 从旧元数据里带过来：用户可能
        # 只是改了一行正文，不该因此丢掉占位符的说明文字。
        old = {spec.name: spec for spec in self._current_specs()}
        fields = self._placeholder_inputs

        def default_for(name: str) -> str | None:
            # 输入框里填的就是这个模板的默认值（预览用的也是它）；没有输入框
            # （正文里刚删掉又加回来之类）就沿用旧元数据的默认值。
            if name in fields:
                return fields[name].text()
            return old[name].default if name in old else None

        specs = [
            PlaceholderSpec(
                name=name,
                default=default_for(name),
                description=old[name].description if name in old else None,
            )
            for name in declared_names(body)
        ]
        self._store.save(
            TemplateInput(
                id=template_id,
                name=template_id,
                description="",
                trusted=self.trusted_check.isChecked(),
                placeholders=specs,
                body=body,
            )
        )
        # 走一遍 reload/select：占位符元数据可能刚被正文改动，重载后界面与磁盘一致。
        self.reload()

    def delete_current(self) -> None:
        if self._current_id is None:
            return
        template_id = self._current_id
        answer = QMessageBox.question(
            self,
            "删除模板",
            f"确定删除模板 {template_id}？正文文件与索引项都会被移除。",
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        self._store.remove(template_id)
        self._current_id = None
        self.reload()

    def import_from_file(self) -> None:
        """导入外部 .tpl.sh：正文原样收下（只做 LF 归一化），id 从文件名清洗而来。"""
        path, _ = QFileDialog.getOpenFileName(self, "导入模板", "", "shell 模板 (*.tpl.sh *.sh)")
        if not path:
            return
        source = Path(path)
        body = to_lf(source.read_text(encoding="utf-8"))
        template_id = _template_id_from_path(source)
        if self._row_for(template_id) >= 0:
            answer = QMessageBox.question(
                self, "模板已存在", f"模板 {template_id} 已存在，用导入的内容覆盖？"
            )
            if answer != QMessageBox.StandardButton.Yes:
                return
        self._store.save(
            TemplateInput(
                id=template_id,
                name=template_id,
                description="",
                trusted=False,                     # 外来脚本一律先按不可信处理
                placeholders=[PlaceholderSpec(name=n) for n in declared_names(body)],
                body=body,
            )
        )
        self.reload()
        self.select(template_id)

    def create_current(self) -> None:
        """新建空模板：沿用内置骨架的锚点，省得用户从零记锚点格式。"""
        template_id, accepted = QInputDialog.getText(self, "新建模板", "模板 id（小写字母数字与 -）")
        if not accepted or not template_id.strip():
            return
        template_id = _template_id_from_path(Path(template_id.strip()))
        if self._row_for(template_id) >= 0:
            QMessageBox.warning(self, "模板已存在", f"模板 {template_id} 已存在，请换一个 id。")
            return
        try:
            self._store.save(
                TemplateInput(
                    id=template_id,
                    name=template_id,
                    description="",
                    trusted=False,
                    placeholders=[],
                    body="#!/usr/bin/env bash\nset -euo pipefail\n\n# @@TU:BODY@@\n",
                )
            )
        except ValueError as exc:                   # id 非法（store 的校验）
            QMessageBox.warning(self, "id 非法", str(exc))
            return
        self.reload()
        self.select(template_id)

    # ── 交给 LoopInput ─────────────────────────────────────────────────
    def to_template_spec(self) -> TemplateSpec:
        """当前模板 → TemplateSpec。锚点取自渲染后的骨架，与 CLI 同一条路径。

        直接扫正文会把 `{{ }}` 里的字符串一起扫进来；先渲染再取锚点，拿到的才是
        生成脚本里真正必须保留的那几个。
        """
        if self._current_id is None:
            raise ValueError("还没有选中模板")
        body = to_lf(self.body_edit.toPlainText())
        specs = self._declared_specs(body, self._current_specs())
        values = self.placeholder_values()
        skeleton = render_template(body, specs, values)
        return TemplateSpec(
            id=self._current_id,
            body=body,
            anchors=extract_anchors(skeleton),
            trusted=self.trusted_check.isChecked(),
            placeholders=tuple(specs),
        )
