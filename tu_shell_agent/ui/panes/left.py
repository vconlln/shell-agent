"""左栏：方案与本次运行参数（骨架占位，内容由后续任务填充）。"""

from __future__ import annotations

from PySide6.QtWidgets import QLabel, QVBoxLayout, QWidget


class LeftPane(QWidget):
    """方案 + 本次运行参数的输入区。本任务只装配占位标题。"""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("leftPane")
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("方案与运行参数"))
        layout.addStretch(1)
