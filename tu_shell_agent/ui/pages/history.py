"""历史运行列表与回放页（骨架占位，内容由后续任务填充）。"""

from __future__ import annotations

from PySide6.QtWidgets import QLabel, QVBoxLayout, QWidget


class HistoryPage(QWidget):
    """历次运行的列表与三区回放。本任务只装配占位标题。"""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("historyPage")
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("历史运行"))
        layout.addStretch(1)
