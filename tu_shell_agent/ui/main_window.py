"""主窗口：三区 + 底栏 + 两个独立页。只做布局与装配，业务在 run_controller。"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QHBoxLayout, QListWidget, QMainWindow, QPushButton, QSplitter, QTabWidget, QVBoxLayout, QWidget,
)

from .panes.center import CenterPane
from .panes.left import LeftPane
from .panes.right import RightPane
from .panes.templates import TemplatesPane
from .pages.history import HistoryPage
from .pages.selfcheck import SelfCheckPage
from .pages.settings_page import SettingsPage


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("tu-shell-agent — 方案 → shell 脚本 → 执行 → 校验")
        self.resize(1440, 900)

        self.left_pane = LeftPane()
        self.left_pane.setObjectName("leftPane")
        self.templates_pane = TemplatesPane()
        self.templates_pane.setObjectName("templatesPane")
        self.center_pane = CenterPane()
        self.center_pane.setObjectName("centerPane")
        self.right_pane = RightPane()
        self.right_pane.setObjectName("rightPane")

        left_column = QSplitter(Qt.Orientation.Vertical)
        left_column.addWidget(self.left_pane)
        left_column.addWidget(self.templates_pane)
        left_column.setSizes([400, 500])

        self.splitter = QSplitter(Qt.Orientation.Horizontal)
        self.splitter.setObjectName("mainSplitter")   # 测试契约
        self.splitter.addWidget(left_column)
        self.splitter.addWidget(self.center_pane)
        self.splitter.addWidget(self.right_pane)
        self.splitter.setSizes([360, 620, 460])

        self.history_list = QListWidget()
        self.history_list.setObjectName("historyList")

        self.side_pages = QTabWidget()
        self.side_pages.setObjectName("sidePages")     # 测试契约
        self.side_pages.addTab(SelfCheckPage(), "环境自检")
        self.side_pages.addTab(SettingsPage(), "设置")

        self.start_button = QPushButton("开始")
        self.cancel_button = QPushButton("取消")
        self.continue_button = QPushButton("继续修复")
        self.verify_button = QPushButton("改后重跑")
        self.open_dir_button = QPushButton("打开运行目录")
        bottom = QHBoxLayout()
        for button in (self.start_button, self.cancel_button, self.continue_button,
                       self.verify_button, self.open_dir_button):
            bottom.addWidget(button)
        bottom.addStretch(1)

        # 底部一行：左边历史运行列表，右边两个独立页
        bottom_row = QHBoxLayout()
        bottom_row.addWidget(self.history_list, 1)
        bottom_row.addWidget(self.side_pages, 2)

        root_layout = QVBoxLayout()
        root_layout.addWidget(self.splitter, 1)
        root_layout.addLayout(bottom_row, 1)
        root_layout.addLayout(bottom)

        root = QWidget()
        root.setLayout(root_layout)
        self.setCentralWidget(root)

    def closeEvent(self, event) -> None:  # noqa: N802 - Qt 命名
        # 任务 9 会在这里接上 worker 的取消与收尾；此刻必须是安全的 no-op
        super().closeEvent(event)
