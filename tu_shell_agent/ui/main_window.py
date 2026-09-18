"""主窗口：三区 + 底栏 + 两个独立页。只做布局与装配，业务在 run_controller。"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QPushButton,
    QSplitter,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from .panes.center import CenterPane
from .panes.left import LeftPane
from .panes.right import RightPane
from .panes.templates import TemplatesPane
from .pages.history import HistoryPage
from .pages.selfcheck import SelfCheckPage
from .pages.settings_page import SettingsPage
from .settings import AppSettings, default_settings_path


class MainWindow(QMainWindow):
    """三区（方案/脚本/校验）+ 底栏（历史 + 两个独立页）+ 按钮条。

    `wire_controller=False` 是给控制器自己用的：RunController 在没有传入 window 时会建一个
    主窗口，此时若窗口再建一个控制器，就会出现两个控制器同时接管同一批按钮与同一个历史列表
    （回放会被处理两遍）。生产入口 `ui/app.py` 走默认值，窗口自己装配控制器。
    """

    def __init__(self, *, wire_controller: bool = True, settings: AppSettings | None = None) -> None:
        super().__init__()
        self.setWindowTitle("tu-shell-agent — 方案 → shell 脚本 → 执行 → 校验")
        self.resize(1440, 900)

        self.settings = settings if settings is not None else AppSettings.load(default_settings_path())

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

        # 底栏左边是历史运行（列表 + 回放），右边是两个独立页。
        # 列表控件本身在 HistoryPage 里，objectName 仍是 historyList（界面骨架测试的契约）。
        self.history_page = HistoryPage(run_root=self.settings.run_root)

        self.selfcheck_page = SelfCheckPage()
        self.settings_page = SettingsPage()
        self.settings_page.set_settings(self.settings)
        self.side_pages = QTabWidget()
        self.side_pages.setObjectName("sidePages")     # 测试契约
        self.side_pages.addTab(self.selfcheck_page, "环境自检")
        self.side_pages.addTab(self.settings_page, "设置")

        self.start_button = QPushButton("开始")
        self.cancel_button = QPushButton("取消")
        self.continue_button = QPushButton("继续修复")
        self.verify_button = QPushButton("改后重跑")
        self.open_dir_button = QPushButton("打开运行目录")
        bottom = QHBoxLayout()
        for button in (self.start_button, self.cancel_button, self.continue_button,
                       self.verify_button, self.open_dir_button):
            bottom.addWidget(button)
        self.status_label = QLabel("就绪")
        self.status_label.setObjectName("statusLabel")
        bottom.addWidget(self.status_label, 1)

        bottom_row = QHBoxLayout()
        bottom_row.addWidget(self.history_page, 1)
        bottom_row.addWidget(self.side_pages, 2)

        root_layout = QVBoxLayout()
        root_layout.addWidget(self.splitter, 1)
        root_layout.addLayout(bottom_row, 1)
        root_layout.addLayout(bottom)

        root = QWidget()
        root.setLayout(root_layout)
        self.setCentralWidget(root)

        # 设置里已经存过的路径要体现在界面上，否则用户会以为设置没生效：
        # 运行根只在左栏为空时预填（别覆盖用户刚敲进去的），阻断级别直接同步给右栏。
        if self.settings.run_root and not self.left_pane.run_root_edit.text().strip():
            self.left_pane.run_root_edit.setText(self.settings.run_root)
        self.right_pane.blocking_level = self.settings.blocking_level
        self.settings_page.saved.connect(self._on_settings_saved)

        self.controller = None
        if wire_controller:
            self._wire_controller()

    # ── 接线 ──────────────────────────────────────────────────────
    def _wire_controller(self) -> None:
        """装配控制器。按钮接线在控制器里做：它才是"我这个控件归谁管"的主人，
        而且测试直接构造 RunController 时也要能拿到同一套接线。"""
        from .run_controller import RunController

        RunController(
            window=self,
            settings=self.settings,
            run_root=self.settings.run_root,
            # 生产语义：非 trusted 模板的每一次执行都要过人工闸门（规格 §9）。
            # auto_confirm=False + confirm_answer=None → 控制器弹真实对话框；
            # trusted 模板由引擎自己跳过确认，根本不会走到这里。
            auto_confirm=False,
            confirm_answer=None,
        )

    def _on_settings_saved(self, _path: str) -> None:
        """设置保存后重新装载：只影响**之后**的运行，不打断正在跑的。"""
        self.settings = self.settings_page.collect()
        self.history_page.run_root = self.settings.run_root
        self.right_pane.blocking_level = self.settings.blocking_level
        if self.settings.run_root:
            self.left_pane.run_root_edit.setText(self.settings.run_root)
        self.history_page.reload()
        self.set_status(f"设置已保存：{_path}")

    # ── 供控制器调用 ──────────────────────────────────────────────
    def set_status(self, text: str) -> None:
        self.status_label.setText(text)

    def set_running(self, running: bool) -> None:
        """运行期间只留"取消"可点：重复点"开始"会在同一个运行目录上再起一个 worker。"""
        self.start_button.setEnabled(not running)
        self.cancel_button.setEnabled(running)
        self.continue_button.setEnabled(not running)
        self.verify_button.setEnabled(not running)
        self.open_dir_button.setEnabled(not running)

    def closeEvent(self, event) -> None:  # noqa: N802 - Qt 命名
        # 关闭时先把 worker 收掉：QThread 还在跑就被析构会让进程崩在退出路径上。
        controller = getattr(self, "controller", None)
        if controller is not None:
            controller.shutdown()
        super().closeEvent(event)
