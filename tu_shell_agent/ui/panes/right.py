"""右栏：shellcheck 报告与执行输出（骨架占位，内容由后续任务填充）。"""

from __future__ import annotations

from PySide6.QtWidgets import QLabel, QVBoxLayout, QWidget


class RightPane(QWidget):
    """shellcheck 报告 + 执行输出 + 模型取舍说明。本任务只装配占位标题。"""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("rightPane")
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("校验报告与执行输出"))
        layout.addStretch(1)
