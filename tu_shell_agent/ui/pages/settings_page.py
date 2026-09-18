"""设置页（骨架占位，内容由后续任务填充）。"""

from __future__ import annotations

from PySide6.QtWidgets import QLabel, QVBoxLayout, QWidget


class SettingsPage(QWidget):
    """模型、超时与运行目录等设置。本任务只装配占位标题。"""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("settingsPage")
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("设置"))
        layout.addStretch(1)
