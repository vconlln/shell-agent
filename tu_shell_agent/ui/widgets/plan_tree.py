"""方案文件树：像 VS Code 左侧那样浏览文件夹、点一下就把某个文件选成方案。

**为什么要有它**（用户 2026-09-20）：左栏原来那块「运行参数（仅本次）」与设置页里的
「运行」一节完全重复（运行根目录、阻断级别、轮次上限、生成/执行超时都在设置里），
留着只是占地方。用户要求把那块换成"选文件夹 → 看里面的文件 → 点一个"的文件树。

**行为约定**（写清楚，免得用起来像在猜）：

- 点一个 **`.md` / `.txt`** 文件 → 直接设为方案（并把正文填进上面的预览）；
- 点其它文件 → 只是选中；**双击**任何文件都当"就选它"（预览会如实报"读不了/不是文本"）；
- 文件夹单击展开（VS Code 是单击展开、双击才是打开，这里保持一致）；
- 隐藏以 `.` 开头的条目：`.git`、`.venv` 这些在方案目录里只会碍眼——不给
  `QDir.Filter.Hidden`，Qt 自己就不列隐藏项，不必手写过滤。

树用的是 Qt 自带的 `QFileSystemModel`：它自带监听文件系统变化（新建/删除文件会自动刷新），
不必自己写轮询。

**两个必须记住的坑**（都是实测出来的，写在这里免得以后又踩）：

1. `QFileSystemModel` 必须调用 `setRootPath()`——只 `setRootIndex()`（视图的根）不够。
   没有 `setRootPath()` 时它的目录读取线程根本不会启动：`directoryLoaded` 永不触发、
   `rowCount()` 永远是 0，看起来就像"树里什么都没有"。
2. 目录内容是**异步**读进来的（后台线程读盘、读完了发 `directoryLoaded`）。所以
   `set_root()` 返回时树里可能还是空的，这是正常的；要等某个文件的方法都得挂在
   `directoryLoaded` 上。测试里也必须等这个信号，不能只转几圈事件循环就断言行数
   （`processEvents()` 转得再快也不给后台线程留出读盘时间）。
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QDir, QModelIndex, Qt, Signal
from PySide6.QtWidgets import (
    QFileSystemModel,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QTreeView,
    QVBoxLayout,
    QWidget,
)

# 这些后缀的文件点一下就当"选它做方案"
PLAN_SUFFIXES = (".md", ".markdown", ".txt")

# 树的最小高度：左栏是滚动区，太矮等于看不见（用户抱怨过"挤压到看不见"）
MIN_HEIGHT = 150


class PlanTree(QWidget):
    """方案文件树 + 一行小工具（选择文件夹 / 回到方案目录 / 刷新）。"""

    plan_chosen = Signal(str)      # 用户在树里选了一个方案文档（携带绝对路径）
    root_changed = Signal(str)     # 换根目录了（调用方可以记进设置）

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("planTreeBox")
        # 自定义 QWidget 子类必须显式开这个属性，QSS 里的 `background-color` 才会真的画出来
        # （默认只有 Qt 自己的控件类会被样式表上色）。这块面板承载整棵树的底色与边框：
        # 视图自己上不了色 —— 它的像素属于 viewport，`QTreeView` 的 background-color 到不了
        # 那里（实测内部取到的是外层卡片色，等于没生效）。
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)

        self.model = QFileSystemModel(self)
        # 不给 Hidden：隐藏项（.git / .venv / .DS_Store）由 Qt 直接不列，省得自己过滤
        self.model.setFilter(
            QDir.Filter.AllDirs | QDir.Filter.Files | QDir.Filter.NoDotAndDotDot
        )
        self.model.setReadOnly(True)
        # 目录读完才会触发；「选中当前方案」这类操作必须等它，否则树里还没这个条目
        self.model.directoryLoaded.connect(self._on_directory_loaded)

        self.view = QTreeView()
        self.view.setObjectName("planTree")
        self.view.setModel(self.model)
        self.view.setHeaderHidden(True)
        self.view.setUniformRowHeights(True)      # 大目录下列表滚动更稳
        self.view.setAnimated(False)
        self.view.setIndentation(14)
        self.view.setExpandsOnDoubleClick(True)
        self.view.setMinimumHeight(MIN_HEIGHT)
        # 只留"名字"那一列：大小/类型/修改时间在方案选择这件事上没有用，只会把名字挤没
        for column in range(1, self.model.columnCount()):
            self.view.hideColumn(column)
        self.view.clicked.connect(self._on_clicked)
        self.view.doubleClicked.connect(self._on_double_clicked)
        self.view.setToolTip(
            "点选方案文档（.md / .txt）即设为方案；双击任何文件也当选中它。\n"
            "以 . 开头的条目已隐藏。"
        )

        self.pick_button = QPushButton("选择文件夹…")
        self.pick_button.setObjectName("planTreePickButton")
        self.pick_button.setToolTip("换一个根目录来浏览（例如你的方案放在别处）")
        self.pick_button.clicked.connect(lambda _checked=False: self.pick_root())
        self.reveal_button = QPushButton("回到方案目录")
        self.reveal_button.setObjectName("planTreeRevealButton")
        self.reveal_button.setToolTip("跳到当前方案所在的目录，并在树里选中它")
        self.reveal_button.clicked.connect(lambda _checked=False: self.reveal_plan())
        self.refresh_button = QPushButton("刷新")
        self.refresh_button.setObjectName("planTreeRefreshButton")
        self.refresh_button.setToolTip("重新读一遍这个目录（模型本身也会自动跟随文件系统变化）")
        self.refresh_button.clicked.connect(lambda _checked=False: self.refresh())

        self.root_label = QLabel()
        self.root_label.setObjectName("planTreeRoot")
        self.root_label.setProperty("role", "muted")
        self.root_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)

        buttons = QHBoxLayout()
        buttons.setContentsMargins(0, 0, 0, 0)
        buttons.setSpacing(4)
        buttons.addWidget(self.pick_button)
        buttons.addWidget(self.reveal_button)
        buttons.addWidget(self.refresh_button)
        buttons.addStretch(1)

        layout = QVBoxLayout(self)
        # 留出内边距：这块是个带边框的面板（见 theme 里 `#planTreeBox`），
        # 内容贴着边框会显得"挤在框线上"，圆角还会切掉按钮的直角
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(4)
        layout.addLayout(buttons)
        layout.addWidget(self.view, 1)
        layout.addWidget(self.root_label)

        self._root = ""
        self._plan_path = ""
        self._pending_reveal = ""     # 等目录读完再选中的目标（见 _on_directory_loaded）

    # ── 对外 ──────────────────────────────────────────────────────────
    def set_root(self, path: str, notify: bool = True) -> bool:
        """换根目录；目录不存在就返回 False（调用方可以退回默认值）。

        `notify=False` 用于"程序自己定的根目录"（设置里存的那份、或按运行根目录推出来的
        默认值）：那不是用户的选择，不该被记进设置 —— 否则光是启动一次程序就会把推断出来的
        路径写成永久设置，之后用户改了运行根目录，树还指着老地方。
        """
        text = str(path or "").strip()
        if not text or not Path(text).is_dir():
            return False
        index = self.model.index(text)
        if not index.isValid():
            return False
        self._root = str(Path(text))
        # setRootPath 必须在 setRootIndex 之前：它才是启动读取线程、让模型开始列目录的那一步
        self.model.setRootPath(self._root)
        self.view.setRootIndex(index)
        self.view.expand(index)
        self.root_label.setText(f"目录：{self._root}")
        self.root_label.setToolTip(self._root)
        if notify:
            self.root_changed.emit(self._root)
        return True

    def root(self) -> str:
        return self._root

    def refresh(self) -> None:
        """重新读一遍当前根目录。

        `QFileSystemModel` 没有公开的 refresh()，而重复 `setRootPath(同一个路径)` 会被它
        当成没变化直接忽略；先清空再设回同一个路径，才会真的丢掉缓存重新列一遍。
        """
        if not self._root:
            return
        root = self._root
        self.model.setRootPath("")
        self.model.setRootPath(root)
        self.view.setRootIndex(self.model.index(root))
        self.reveal(self._plan_path)

    def reveal(self, path: str) -> None:
        """在树里展开并选中某个文件（方案换了、或点了「回到方案目录」）。

        目录内容是异步来的，所以这里只记下目标、先尽力做一次；真正确认选中是在
        `_on_directory_loaded` 里补做的。
        """
        text = str(path or "").strip()
        if not text:
            return
        target = Path(text)
        if not target.exists():
            return
        folder = target if target.is_dir() else target.parent
        if not self._root or not _is_within(folder, Path(self._root)):
            # 当前根看不到它：把根换到它所在的目录（用户刚选了别处的方案时会发生）
            if not self.set_root(str(folder)):
                return
        self._pending_reveal = str(target)
        self._select(target)

    def reveal_plan(self) -> None:
        if self._plan_path:
            self.reveal(self._plan_path)

    def pick_root(self) -> None:
        """弹目录选择框换根目录（取消时什么都不做）。"""
        from PySide6.QtWidgets import QFileDialog

        chosen = QFileDialog.getExistingDirectory(
            self, "选择要在左侧浏览的文件夹", self._root or str(Path.home())
        )
        if chosen:
            self.set_root(chosen)
            self.reveal(self._plan_path)

    def set_plan_path(self, path: str) -> None:
        """记住当前方案（供「回到方案目录」与刷新用），不触发信号。"""
        self._plan_path = str(path or "")

    def plan_path(self) -> str:
        """当前记住的方案路径（树只用来定位，不负责读文件）。"""
        return self._plan_path

    # ── 内部 ──────────────────────────────────────────────────────────
    def _select(self, target: Path) -> None:
        """选中目标并让它进到可视区里；父目录由 `scrollTo` 负责展开。

        不需要自己逐级 `expand()`：`QTreeView.scrollTo()` 为了让目标可见，本身就会把
        沿途的父目录展开（实测：手动展开那几行删掉后，"深层方案 → 子目录已展开、选中行在
        可视区内"依然成立）。少一份自己维护的展开逻辑，就少一处会和视图状态不同步的地方。
        """
        index = self.model.index(str(target))
        if not index.isValid():
            return
        self.view.setCurrentIndex(index)
        self.view.scrollTo(index, QTreeView.ScrollHint.PositionAtCenter)

    def _on_directory_loaded(self, path: str) -> None:
        """某个目录读完了：如果正在等一个待选中的目标，这时才真的能选中它。"""
        pending = self._pending_reveal
        if not pending:
            return
        target = Path(pending)
        folder = target if target.is_dir() else target.parent
        if Path(path) == folder or _is_within(folder, Path(path)):
            self._select(target)
            self._pending_reveal = ""

    def _on_clicked(self, index: QModelIndex) -> None:
        if not index.isValid():
            return
        path = self.model.filePath(index)
        if Path(path).is_dir():
            # 视图自己也会按 expandsOnDoubleClick 处理双击，这里管的是单击
            self.view.setExpanded(index, not self.view.isExpanded(index))
            return
        if Path(path).suffix.lower() in PLAN_SUFFIXES:
            self.plan_chosen.emit(path)

    def _on_double_clicked(self, index: QModelIndex) -> None:
        if not index.isValid():
            return
        path = self.model.filePath(index)
        if path and not Path(path).is_dir():
            self.plan_chosen.emit(path)


def _is_within(child: Path, parent: Path) -> bool:
    """`child` 是否在 `parent` 里面（用于判断树的根能不能看到这个文件）。"""
    try:
        child.resolve().relative_to(parent.resolve())
    except (ValueError, OSError):
        return False
    return True
