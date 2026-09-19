"""环境自检页：显示三件套的版本/路径与问题清单，并允许重跑探测。"""

from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QLabel, QPlainTextEdit, QPushButton, QVBoxLayout, QWidget

from ..widgets.scroll import form_container, scrollable
from ...types import DetectionReport

_TOOLS = ("opencode", "bash", "shellcheck")


class SelfCheckPage(QWidget):
    """三件套（opencode / Git Bash / shellcheck）的自检结果。

    页面只做两件事：如实显示，以及把"用户要求重跑"这件事发出去。
    真实探测要起子进程、可能耗时数秒，必须由控制器放在工作线程里跑——
    界面线程被它卡住的话，用户在自检期间连窗口都拖不动（规格 §9：三件套缺一不可）。
    """

    recheck_requested = Signal()      # 用户点了"重新检测"；控制器接上真实探测后再调 render()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("selfCheckPage")     # main_window 与骨架测试的契约，不要改名
        self._report: DetectionReport | None = None

        # 一行状态 + 一个按钮；**详情框只在有事要说时才出现**。
        # 原来那行"三件套缺一不可：…（规格 §9）"是把规格原文贴给用户看 —— 用户要的是结论，
        # 不是规矩；自检通过时更没必要占一大片地方（用户问的就是这个）。
        self.status_label = QLabel("尚未检测")
        self.status_label.setObjectName("selfCheckStatus")
        self.status_label.setWordWrap(True)
        self.status_label.setProperty("role", "hint")
        self.recheck_button = QPushButton("重新检测")
        self.text = QPlainTextEdit()
        self.text.setObjectName("selfCheckDetail")
        self.text.setReadOnly(True)             # 自检结果是"呈堂证供"，不允许用户改
        self.text.setMinimumHeight(90)
        self.text.setVisible(False)             # 默认收起：通过时一行就够

        # 表单进滚动区：空间不够时滚动，而不是把控件压扁（见 widgets/scroll.py）
        content, layout = form_container()
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(scrollable(content))
        layout.addWidget(self.status_label)
        layout.addWidget(self.recheck_button)
        layout.addWidget(self.text, 1)
        layout.addStretch(1)

        self.recheck_button.clicked.connect(self._on_recheck_clicked)

    def render(self, report: DetectionReport) -> None:
        """把一次探测结果铺到页面上。探测失败也要调它——problems 里会写清为什么失败。

        显示策略：**一行结论**（通过/缺几项/几条提示）始终在；详情框只在有 problems 或
        warnings 时展开 —— 自检通过时它是三条版本信息，已经在那一行里说完了，
        再占一大片空间没有意义。
        """
        self._report = report
        self.text.setPlainText(self._format(report))
        self.text.setVisible(bool(report.problems or report.warnings))
        self.status_label.setText(self._status_line(report))

    @staticmethod
    def _status_line(report: DetectionReport) -> str:
        """一行说清结论：通过就把三件套的版本列出来，否则说缺什么。"""
        if report.problems:
            return f"⚠ {len(report.problems)} 个问题需要处理（见下方）"
        versions = " · ".join(
            f"{name} {tool.version}" for name in _TOOLS
            if (tool := getattr(report, name)) is not None
        )
        if report.warnings:
            return f"✓ 环境可用：{versions}（另有 {len(report.warnings)} 条提示）"
        return f"✓ 环境就绪：{versions}"

    def report(self) -> DetectionReport | None:
        """上一次被渲染的探测结果（没检测过则为 None）；控制器据此构造真实工具链。"""
        return self._report

    def summary_text(self) -> str:
        return self.text.toPlainText()

    def has_problems(self) -> bool:
        """没检测过时返回 False：不能把"还不知道"谎报成"有问题"。

        注意只算 problems：warnings 是"提示"，不阻断运行（例如 opencode 没保存凭据，
        但用户可能用环境变量给了 key），把它算成"有问题"会让自检常态化地报红。
        """
        return self._report is not None and bool(self._report.problems)

    def has_warnings(self) -> bool:
        """有没有提示项（与 problems 分开，界面文案也不一样）。"""
        return self._report is not None and bool(self._report.warnings)

    def _on_recheck_clicked(self) -> None:
        # 不直接连 recheck_requested.emit：clicked 带一个 bool 实参，转一道手才不会被它噎住
        self.recheck_requested.emit()

    @staticmethod
    def _format(report: DetectionReport) -> str:
        lines: list[str] = []
        for name in _TOOLS:
            tool = getattr(report, name)
            if tool is None:
                lines.append(f"{name}: 未找到")
            else:
                lines.append(f"{name}: {tool.version}  ({tool.path})")
        if report.problems:
            lines.append("")
            lines.append("问题：")
            lines.extend(f"- {problem}" for problem in report.problems)
        if report.warnings:
            # 与"问题"分开写：提示不阻断运行，混在一起会让人以为自检没过。
            lines.append("")
            lines.append("提示（不阻断运行）：")
            lines.extend(f"- {warning}" for warning in report.warnings)
        return "\n".join(lines)
