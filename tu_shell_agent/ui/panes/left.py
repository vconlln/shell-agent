"""左栏：方案选择与预览 + 本次运行参数。

只收集输入并做廉价校验，**不发起运行**（运行由任务 9 的 RunController 负责），
因此这里不碰网络、子进程，也不做建目录之类的磁盘写操作：写盘的失败信息
在引擎侧才有上下文（例如「运行根是文件」「无权限」），在界面里抢先试一遍
只会让同一件事有两处说法，且会在用户敲路径的过程中反复触发。
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Signal
from ..widgets.scroll import form_container, scrollable
from PySide6.QtWidgets import (
    QComboBox, QFileDialog, QFormLayout, QHBoxLayout, QLabel, QLineEdit, QPlainTextEdit,
    QPushButton, QSpinBox, QVBoxLayout, QWidget,
)

from ...types import RunConfig, Severity

# 与 types.SEVERITY_RANK 同源的四个级别；顺序即下拉框顺序（由重到轻）
BLOCKING_LEVELS: tuple[Severity, ...] = ("error", "warning", "info", "style")

# 未读到方案正文时预览区的开场白：区别于「方案文件空」
_PREVIEW_IDLE = "（尚未选择方案文档）"


def _section(text: str) -> QLabel:
    """分组小标题：QSS 按 role 属性统一成"小号大写次级色"，语义写在属性上而不是各写样式。"""
    label = QLabel(text)
    label.setProperty("role", "section")
    return label


def _absolute_run_root(text: str) -> str:
    """运行根一律展开 `~` 并绝对化。

    不处理的话 `~/runs` 会被引擎当成**相对**路径（`Path("~/runs")` 不是家目录），
    于是在进程 CWD 下建一个名字就叫 `~` 的目录、整次运行都落在那里，界面毫无提示。
    相对路径同理（`runs` → CWD/runs）。
    """
    stripped = text.strip()
    if not stripped:
        return ""
    return str(Path(stripped).expanduser().absolute())


class LeftPane(QWidget):
    """方案 + 本次运行参数的输入区。"""

    plan_changed = Signal(str)          # 选择方案后发出（绝对路径）

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("leftPane")   # main_window 与骨架测试依赖的名字，不要改
        self._plan_path: str = ""        # 空串 = 未选方案
        self._plan_text: str = ""        # 方案正文；读失败时为空（错误说明只在预览里显示）
        self._plan_error: str = ""       # 非空 = 上次读取失败，validate() 要如实报出来

        self.plan_edit = QLineEdit()
        self.plan_edit.setObjectName("planEdit")
        self.plan_edit.setReadOnly(True)          # 路径只能由文件对话框或 set_plan 写入
        self.plan_edit.setPlaceholderText("选择一个方案文档（.md / .txt）")
        browse_button = QPushButton("选择方案…")
        browse_button.clicked.connect(self._browse_plan)

        plan_row = QHBoxLayout()
        plan_row.addWidget(self.plan_edit, 1)
        plan_row.addWidget(browse_button)

        self.plan_preview = QPlainTextEdit()
        self.plan_preview.setObjectName("planPreview")
        self.plan_preview.setReadOnly(True)
        # 最小高度：没有它的时候，窗口一缩小 QVBoxLayout 会把这个预览压到 12px（实测），
        # 方案正文等于看不见 —— 用户报的"挤压到看不见"就是这个。
        self.plan_preview.setMinimumHeight(90)
        self.plan_preview.setPlainText(_PREVIEW_IDLE)

        self.run_root_edit = QLineEdit()
        self.run_root_edit.setObjectName("runRootEdit")

        self.blocking_combo = QComboBox()
        self.blocking_combo.setObjectName("blockingCombo")
        self.blocking_combo.addItems(list(BLOCKING_LEVELS))
        # 默认 info 而非 warning：实测 SC2086（变量未加引号）是 info 级，
        # 以 warning 为默认会让这类真实隐患「只展示、不修」（规格 §11）。
        self.blocking_combo.setCurrentText("info")

        self.max_rounds_spin = QSpinBox()
        self.max_rounds_spin.setObjectName("maxRoundsSpin")
        self.max_rounds_spin.setRange(1, 10)      # 允许比规格的 3 轮更宽，但下限 1：0 轮等于不生成脚本
        self.max_rounds_spin.setValue(3)
        # 说清 1 意味着什么：真实全链路冒烟里，用户保存的 1 轮把"模型偶尔把契约标记写缺"
        # 从"引擎自己回灌重试就能修"变成了直接 needs_human —— 而界面上当时没有任何提示。
        self.max_rounds_spin.setToolTip(
            "最多生成几轮。大于 1 时，契约不完整或 shellcheck 没过会把失败原因回灌给模型重试；\n"
            "填 1 表示**一次不成即停**（没有重试机会），建议留在默认的 3。"
        )

        self.generate_timeout_spin = QSpinBox()
        self.generate_timeout_spin.setObjectName("generateTimeoutSpin")
        self.generate_timeout_spin.setRange(1_000, 3_600_000)
        self.generate_timeout_spin.setSingleStep(10_000)
        self.generate_timeout_spin.setSuffix(" ms")
        self.generate_timeout_spin.setValue(300_000)

        self.execute_timeout_spin = QSpinBox()
        self.execute_timeout_spin.setObjectName("executeTimeoutSpin")
        self.execute_timeout_spin.setRange(1_000, 3_600_000)
        self.execute_timeout_spin.setSingleStep(10_000)
        self.execute_timeout_spin.setSuffix(" ms")
        self.execute_timeout_spin.setValue(120_000)

        run_form = QFormLayout()
        run_form.addRow("运行根目录", self.run_root_edit)
        run_form.addRow("阻断级别", self.blocking_combo)
        run_form.addRow("轮次上限", self.max_rounds_spin)
        run_form.addRow("生成超时", self.generate_timeout_spin)
        run_form.addRow("执行超时", self.execute_timeout_spin)

        # 表单进滚动区：左栏内容需要 576px，靠"压缩控件"适应高度会把输入框压到 13px
        # （见 widgets/scroll.py 的说明）。现在按需要出滚动条，控件保持正常高度。
        content, layout = form_container()
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(scrollable(content))
        layout.addWidget(_section("方案文档"))
        layout.addLayout(plan_row)
        layout.addWidget(self.plan_preview, 1)
        layout.addWidget(_section("补充要求（本次运行临时追加）"))
        self.extra_edit = QPlainTextEdit()
        self.extra_edit.setObjectName("extraInstructionEdit")
        self.extra_edit.setPlaceholderText(
            "要额外叮嘱的话，例如：别动 logs/ 目录；先备份再删除。\n"
            "会作为独立一段进提示词，与方案冲突时以这里为准；留空则一个字都不加。"
        )
        self.extra_edit.setFixedHeight(64)
        layout.addWidget(self.extra_edit)

        layout.addWidget(_section("运行参数（仅本次）"))
        layout.addLayout(run_form)
        # 组件路径（opencode / Git Bash / shellcheck）只在设置页改，这里只读不自检

    # ---- 方案 -----------------------------------------------------------------

    def set_plan(self, path: str) -> None:
        """选定方案：读文件填预览；读不了就在预览里如实写错误，路径照样记下。"""
        self._plan_path = str(path)
        self.plan_edit.setText(self._plan_path)
        if not self._plan_path:
            self._plan_error = ""
            self._plan_text = ""
            self.plan_preview.setPlainText(_PREVIEW_IDLE)
            self.plan_changed.emit("")
            return
        self._reload_preview()
        self.plan_changed.emit(self._plan_path)

    def _browse_plan(self) -> None:
        """文件对话框选方案。用静态方法而不是实例方法，避免为了一个弹窗持有 QFileDialog。"""
        chosen, _ = QFileDialog.getOpenFileName(
            self, "选择方案文档", self._plan_path or str(Path.home()), "方案文档 (*.md *.txt);;全部文件 (*)",
        )
        if chosen:                       # 取消时返回空串，不能把已选方案清掉
            self.set_plan(chosen)

    def _reload_preview(self) -> None:
        """把方案正文读进预览，或把失败原因写进预览。"""
        try:
            text = Path(self._plan_path).read_text(encoding="utf-8")
        except UnicodeDecodeError:
            # 编码问题与"读不到"是两回事：Windows 记事本另存为 ANSI(GBK)/Unicode(UTF-16)
            # 都会落到这里，笼统说"读不到"会让用户去查权限，而真正要做的是另存为 UTF-8。
            self._plan_error = (
                f"方案文档不是 UTF-8 编码（{Path(self._plan_path).name}）："
                "请用编辑器另存为 UTF-8 后重试"
            )
            self._plan_text = ""
            self.plan_preview.setPlainText(self._plan_error)
            return
        except OSError as error:
            # 方案是给人看的，读不了就是读不了，先说清原因再谈运行
            self._plan_error = f"读不到方案文档：{error}"
            self._plan_text = ""
            self.plan_preview.setPlainText(self._plan_error)
            return
        self._plan_error = ""
        self._plan_text = text
        self.plan_preview.setPlainText(text)

    def plan_path(self) -> str:
        """已选方案的路径；空串表示未选。"""
        return self._plan_path

    def plan_text(self) -> str:
        """方案正文；未选或读失败时为空串（预览区里显示的提示语不算正文）。"""
        return self._plan_text

    def extra_instruction(self) -> str:
        """用户临时追加的要求（可为空）。空串时提示词里不会多出一个空段落。"""
        return self.extra_edit.toPlainText()

    # ---- 运行参数 -------------------------------------------------------------

    def to_run_config(self) -> RunConfig:
        """本次运行参数。组件路径由设置页与 selfcheck 决定，这里一律留空。

        所以不传 path 覆盖：传了就等于给了引擎一个「界面上没显示过的路径」，
        与规格 §12「组件路径只出现在设置页，避免两处可改」冲突。
        """
        level = self.blocking_combo.currentText()
        return RunConfig(
            run_root=_absolute_run_root(self.run_root_edit.text()),
            max_rounds=self.max_rounds_spin.value(),
            generate_timeout_ms=self.generate_timeout_spin.value(),
            execute_timeout_ms=self.execute_timeout_spin.value(),
            blocking_level=level if level in BLOCKING_LEVELS else "info",
        )

    def validate(self) -> list[str]:
        """返回全部问题；文案带上「方案」「运行根」，用户才能一眼定位到是哪一格。

        只查存在性、可读性与「是不是文件」这类廉价事实：真正的可写性要等引擎建运行目录时
        才有结论，界面里先试一遍会在用户敲路径的过程中反复误报。
        """
        problems: list[str] = []
        path = self._plan_path
        if not path:
            problems.append("未选择方案文档")
        elif self._plan_error:
            # 原样带上读到失败时的原因（编码/权限/文件没了），不要在这里改写成笼统的
            # "读不到" —— 那会把"请另存为 UTF-8"这种可操作的话丢掉。
            problems.append(self._plan_error)
        elif not Path(path).is_file():
            problems.append(f"方案文档读不到：{path}")
        elif not self._plan_text.strip():
            # 空方案（0 字节/纯空白）跑起来是"照着一个空方案生成脚本"：引擎对方案不做检查，
            # 它会把空正文写进 run_dir/plan.md，最后还可能落成 succeeded —— 一次没有方案的
            # 运行被记成成功。运行前就拦在这里。
            problems.append(f"方案文档是空的：{path}")

        run_root = _absolute_run_root(self.run_root_edit.text())
        if not run_root:
            problems.append("运行根目录未填写")
        elif Path(run_root).is_file():
            # 目录不存在是正常的（引擎会新建），但指向一个文件时一定跑不起来
            problems.append(f"运行根目录是一个文件，不是目录：{run_root}")
        return problems
