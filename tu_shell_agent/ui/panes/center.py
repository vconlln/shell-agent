"""中栏：脚本与轮次（骨架占位，内容由后续任务填充）。"""

from __future__ import annotations

from PySide6.QtWidgets import QLabel, QVBoxLayout, QWidget


class CenterPane(QWidget):
    """生成的脚本正文与轮次时间线。本任务只装配占位标题。"""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("centerPane")
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("脚本与轮次"))
        layout.addStretch(1)
