"""环境自检页（骨架占位，内容由后续任务填充）。"""

from __future__ import annotations

from PySide6.QtWidgets import QLabel, QVBoxLayout, QWidget


class SelfCheckPage(QWidget):
    """opencode / Git Bash / shellcheck 三件套自检。本任务只装配占位标题。"""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("selfCheckPage")
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("环境自检"))
        layout.addStretch(1)
