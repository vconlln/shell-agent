"""环境自检页：显示三件套的版本/路径与问题清单，并允许重跑探测。"""

from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QLabel, QPlainTextEdit, QPushButton, QVBoxLayout, QWidget

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

        self.hint = QLabel("三件套缺一不可：opencode / Git Bash / shellcheck（规格 §9）")
        self.hint.setWordWrap(True)
        self.recheck_button = QPushButton("重新检测")
        self.text = QPlainTextEdit()
        self.text.setReadOnly(True)             # 自检结果是"呈堂证供"，不允许用户改
        self.text.setPlainText("尚未检测。点击「重新检测」开始。")

        layout = QVBoxLayout(self)
        layout.addWidget(self.hint)
        layout.addWidget(self.recheck_button)
        layout.addWidget(self.text, 1)

        self.recheck_button.clicked.connect(self._on_recheck_clicked)

    def render(self, report: DetectionReport) -> None:
        """把一次探测结果铺到页面上。探测失败也要调它——problems 里会写清为什么失败。"""
        self._report = report
        self.text.setPlainText(self._format(report))

    def report(self) -> DetectionReport | None:
        """上一次被渲染的探测结果（没检测过则为 None）；控制器据此构造真实工具链。"""
        return self._report

    def summary_text(self) -> str:
        return self.text.toPlainText()

    def has_problems(self) -> bool:
        """没检测过时返回 False：不能把"还不知道"谎报成"有问题"。"""
        return self._report is not None and bool(self._report.problems)

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
        return "\n".join(lines)
