"""模板库面板（骨架占位，内容由后续任务填充）。"""

from __future__ import annotations

from PySide6.QtWidgets import QLabel, QVBoxLayout, QWidget


class TemplatesPane(QWidget):
    """脚本模板的列表、正文与占位符编辑。本任务只装配占位标题。"""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("templatesPane")
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("模板库"))
        layout.addStretch(1)
