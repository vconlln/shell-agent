"""方案文件夹文件树：像 VS Code 左侧那样点选方案文档。

用户 2026-09-20 睡前的要求："把这部分替换成那种选择了哪个文件夹的那种，类似于 vscode
的左侧文件树的那种效果"。

这个文件里最重要的不是"能列出文件"，而是**异步加载**这件事：`QFileSystemModel` 在后台
线程读目录，`set_root()` 返回时树里可能还是空的。历史上这里翻过一次车 —— 忘了调用
`setRootPath()`，导致模型的读取线程根本没启动：`directoryLoaded` 永不触发、`rowCount()`
永远是 0，界面上就是一棵空树。所以：

- 每条"树里有东西"的用例都必须等 `directoryLoaded`（用 `_wait_for_rows`），
  只转几圈 `processEvents()` 是**等不到**的（它不给后台线程留出读盘时间）；
- 另有一条用例专门钉住 `setRootPath()` 这个调用（去掉它，等待会超时）。
"""

from __future__ import annotations

from pathlib import Path

import pytest
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QFileSystemModel

from tu_shell_agent.ui.widgets.plan_tree import MIN_HEIGHT, PLAN_SUFFIXES, PlanTree


@pytest.fixture
def tree(qtbot) -> PlanTree:
    widget = PlanTree()
    qtbot.addWidget(widget)
    widget.resize(320, 420)
    widget.show()
    return widget


def _wait_for_rows(tree: PlanTree, folder: Path, minimum: int, timeout_ms: int = 8_000) -> int:
    """等某个目录读完并至少出现 `minimum` 行；返回实际行数（不抛异常，方便断言里看数字）。

    不能只转事件循环：目录内容是后台线程读进来的，`processEvents()` 转得再快也不给它时间。
    这里等的是 `directoryLoaded` 这类真实进度，用 `QTest.qWait` 让事件循环真的空转。
    """
    index = tree.model.index(str(folder))
    waited = 0
    while tree.model.rowCount(index) < minimum and waited < timeout_ms:
        QTest.qWait(50)
        waited += 50
    return tree.model.rowCount(index)


def _names(tree: PlanTree, folder: Path) -> list[str]:
    index = tree.model.index(str(folder))
    return sorted(
        tree.model.fileName(tree.model.index(row, 0, index))
        for row in range(tree.model.rowCount(index))
    )


def _folder(tmp_path: Path) -> Path:
    """一个典型方案目录：两份方案 + 一份非方案 + 隐藏项 + 子目录里的一份方案。"""
    plans = tmp_path / "plans"
    (plans / "子目录").mkdir(parents=True)
    (plans / "方案一.md").write_text("# 方案一\n\n做点事\n", encoding="utf-8")
    (plans / "方案二.txt").write_text("方案二\n", encoding="utf-8")
    (plans / "说明.pdf").write_text("不是方案", encoding="utf-8")
    (plans / ".hidden.md").write_text("# 藏起来的\n", encoding="utf-8")
    (plans / ".git").mkdir()
    (plans / "子目录" / "深层方案.md").write_text("# 深层方案\n", encoding="utf-8")
    return plans


# ── 根目录 ────────────────────────────────────────────────────────────


def test_set_root_reports_and_emits(tree: PlanTree, tmp_path: Path):
    """换根目录要发信号（主窗口靠它记进设置，下次打开还在原处）。"""
    plans = tmp_path / "plans"
    plans.mkdir()
    seen: list[str] = []
    tree.root_changed.connect(seen.append)

    assert tree.set_root(str(plans)) is True
    assert tree.root() == str(plans)
    assert seen == [str(plans)]


def test_set_root_rejects_missing_and_empty(tree: PlanTree, tmp_path: Path):
    """不存在的目录 / 空串都返回 False，且**不**发信号（免得把坏路径记进设置）。"""
    seen: list[str] = []
    tree.root_changed.connect(seen.append)

    assert tree.set_root(str(tmp_path / "没有这个目录")) is False
    assert tree.set_root("") is False
    assert tree.set_root("   ") is False
    assert tree.root() == ""
    assert seen == []


def test_root_is_an_ordinary_file_and_not_a_directory(tree: PlanTree, tmp_path: Path):
    """给一个普通文件当根也要拒绝（否则用户会得到一棵永远空的树）。"""
    plan = tmp_path / "方案.md"
    plan.write_text("# 方案", encoding="utf-8")

    assert tree.set_root(str(plan)) is False


def test_files_show_up_after_the_directory_loads(tree: PlanTree, tmp_path: Path):
    """目录读完文件就出来了 —— 并且隐藏项不在里面。

    这一条同时是 `setRootPath()` 的回归测试：去掉那一次调用，模型的读取线程不会启动，
    这里会等到超时、行数停在 0（实测就是这么发现的）。
    """
    plans = _folder(tmp_path)
    tree.set_root(str(plans))

    rows = _wait_for_rows(tree, plans, 4)

    assert rows == 4, f"期望 4 个可见条目，实际 {rows}：{_names(tree, plans)}"
    assert _names(tree, plans) == ["子目录", "方案一.md", "方案二.txt", "说明.pdf"]


def test_dot_entries_are_hidden(tree: PlanTree, tmp_path: Path):
    """`.git` / `.venv` / 隐藏的 .md 一律不列：方案目录里这些只会碍眼。"""
    plans = _folder(tmp_path)
    tree.set_root(str(plans))
    _wait_for_rows(tree, plans, 4)

    assert not [name for name in _names(tree, plans) if name.startswith(".")]


def test_nested_folder_loads_when_it_is_expanded(tree: PlanTree, tmp_path: Path):
    """子目录要展开才读（不该在根目录时就递归读完整棵树）。"""
    plans = _folder(tmp_path)
    tree.set_root(str(plans))
    _wait_for_rows(tree, plans, 4)

    sub = plans / "子目录"
    index = tree.model.index(str(sub))
    tree.view.expand(index)

    assert _wait_for_rows(tree, sub, 1) == 1
    assert _names(tree, sub) == ["深层方案.md"]


def test_refresh_rereads_the_same_root(tree: PlanTree, tmp_path: Path):
    """「刷新」要真的重新列一遍目录，而且根目录不变。

    **为什么要关掉文件系统监听来测**：`QFileSystemModel` 默认盯着目录，新建一个文件它
    自己就会把新条目加进来 —— 那样这条用例无论刷新写得对不对都会通过（实测：把刷新实现
    换成"重复 setRootPath"它照样绿）。关掉监听、只留手动刷新，才能测到刷新本身：
    实测此时"重复 setRootPath(同一路径)"是**空操作**（模型认为路径没变，直接忽略），
    必须先 `setRootPath("")` 把它清掉才会真的重读。
    """
    plans = _folder(tmp_path)
    tree.model.setOption(QFileSystemModel.Option.DontWatchForChanges, True)
    tree.set_root(str(plans))
    _wait_for_rows(tree, plans, 4)
    (plans / "新加的.md").write_text("# 新加的\n", encoding="utf-8")

    tree.refresh()

    assert _wait_for_rows(tree, plans, 5) == 5
    assert "新加的.md" in _names(tree, plans)
    assert tree.root() == str(plans)


def test_new_files_appear_without_pressing_refresh(tree: PlanTree, tmp_path: Path):
    """不点刷新也要能看见新文件：模型的目录监听本来就会跟着文件系统变化。

    （「刷新」是给监听失效的场景兜底的：网络盘、Windows 上超出监听数量上限、
    或者工具在别的进程里刚写完。）
    """
    plans = _folder(tmp_path)
    tree.set_root(str(plans))
    _wait_for_rows(tree, plans, 4)

    (plans / "刚写的.md").write_text("# 刚写的\n", encoding="utf-8")

    assert _wait_for_rows(tree, plans, 5) == 5
    assert "刚写的.md" in _names(tree, plans)


def test_refresh_without_a_root_does_nothing(tree: PlanTree):
    """还没设根目录时点刷新不许炸（按钮一直是可点的）。"""
    tree.refresh()          # 不抛异常即通过

    assert tree.root() == ""


# ── 点选 ──────────────────────────────────────────────────────────────


def _row(tree: PlanTree, folder: Path, name: str):
    index = tree.model.index(str(folder))
    for row in range(tree.model.rowCount(index)):
        child = tree.model.index(row, 0, index)
        if tree.model.fileName(child) == name:
            return child
    raise AssertionError(f"{name} 不在树里：{_names(tree, folder)}")


@pytest.mark.parametrize(
    "name", ["方案一.md", "方案二.txt"],
)
def test_clicking_a_plan_file_chooses_it(tree: PlanTree, tmp_path: Path, name: str):
    """点 .md / .txt 就当"选它做方案"（这才是树存在的主要理由）。"""
    plans = _folder(tmp_path)
    tree.set_root(str(plans))
    _wait_for_rows(tree, plans, 4)
    chosen: list[str] = []
    tree.plan_chosen.connect(chosen.append)

    tree._on_clicked(_row(tree, plans, name))

    assert chosen == [str(plans / name)]


def test_clicking_a_non_plan_file_only_selects(tree: PlanTree, tmp_path: Path):
    """点 .pdf 之类只是选中，不把它当方案（预览会去读一个二进制文件）。"""
    plans = _folder(tmp_path)
    tree.set_root(str(plans))
    _wait_for_rows(tree, plans, 4)
    chosen: list[str] = []
    tree.plan_chosen.connect(chosen.append)

    tree._on_clicked(_row(tree, plans, "说明.pdf"))

    assert chosen == []


def test_double_click_chooses_any_file(tree: PlanTree, tmp_path: Path):
    """双击任何文件都当"就选它"（预览会如实报"读不了/不是文本"，用户自己判断）。"""
    plans = _folder(tmp_path)
    tree.set_root(str(plans))
    _wait_for_rows(tree, plans, 4)
    chosen: list[str] = []
    tree.plan_chosen.connect(chosen.append)

    tree._on_double_clicked(_row(tree, plans, "说明.pdf"))

    assert chosen == [str(plans / "说明.pdf")]


def test_clicking_a_folder_toggles_expansion(tree: PlanTree, tmp_path: Path):
    """文件夹单击展开/收起（VS Code 也是单击展开）。"""
    plans = _folder(tmp_path)
    tree.set_root(str(plans))
    _wait_for_rows(tree, plans, 4)
    index = _row(tree, plans, "子目录")
    chosen: list[str] = []
    tree.plan_chosen.connect(chosen.append)

    tree._on_clicked(index)
    expanded_once = tree.view.isExpanded(index)
    tree._on_clicked(index)

    assert expanded_once is True
    assert tree.view.isExpanded(index) is False
    assert chosen == []          # 文件夹不触发选方案


def test_invalid_index_is_ignored(tree: PlanTree, tmp_path: Path):
    """无效索引不许炸、也不许误发选方案信号（模型还在加载时点击就会碰到）。

    注：这一条是**健壮性**用例，不是变异验证出来的 —— 去掉 `isValid()` 判断它照样绿
    （`filePath(无效索引)` 返回空串，空串自然不是方案文件）。判断留着是因为它便宜，
    而且挡住的是以后有人在 `_on_clicked` 里加"取父目录"之类调用时的崩溃。
    """
    from PySide6.QtCore import QModelIndex

    plans = _folder(tmp_path)
    tree.set_root(str(plans))
    chosen: list[str] = []
    tree.plan_chosen.connect(chosen.append)

    tree._on_clicked(QModelIndex())
    tree._on_double_clicked(QModelIndex())

    assert chosen == []


# ── 定位（回到方案目录） ───────────────────────────────────────────────


def test_reveal_selects_a_top_level_plan(tree: PlanTree, tmp_path: Path):
    """方案就在根目录下：选中它。"""
    plans = _folder(tmp_path)
    tree.set_root(str(plans))
    _wait_for_rows(tree, plans, 4)

    tree.reveal(str(plans / "方案一.md"))
    QTest.qWait(200)

    assert tree.model.fileName(tree.view.currentIndex()) == "方案一.md"


def test_reveal_expands_ancestors_for_a_nested_plan(tree: PlanTree, tmp_path: Path):
    """方案藏在子目录里：要一路展开到它并选中。

    不展开的话 `setCurrentIndex` 也会生效，但那一行在树里是**看不见的** ——
    用户点「回到方案目录」会以为没反应。
    """
    plans = _folder(tmp_path)
    tree.set_root(str(plans))
    _wait_for_rows(tree, plans, 4)
    deep = plans / "子目录" / "深层方案.md"

    tree.reveal(str(deep))
    QTest.qWait(400)

    assert tree.view.isExpanded(tree.model.index(str(plans / "子目录"))) is True
    assert tree.model.fileName(tree.view.currentIndex()) == "深层方案.md"


def test_reveal_moves_the_root_when_the_plan_is_outside(tree: PlanTree, tmp_path: Path):
    """方案在别的目录里：把根换到它所在目录，而不是"选中失败、什么都没发生"。"""
    plans = _folder(tmp_path)
    tree.set_root(str(plans))
    _wait_for_rows(tree, plans, 4)
    elsewhere = tmp_path / "别处"
    elsewhere.mkdir()
    other_plan = elsewhere / "别处的方案.md"
    other_plan.write_text("# 别处的方案\n", encoding="utf-8")

    tree.reveal(str(other_plan))
    QTest.qWait(400)

    assert tree.root() == str(elsewhere)
    assert tree.model.fileName(tree.view.currentIndex()) == "别处的方案.md"


def test_reveal_ignores_missing_paths(tree: PlanTree, tmp_path: Path):
    """路径不存在就什么都不做（根目录不许被换到一个不存在的地方）。"""
    plans = _folder(tmp_path)
    tree.set_root(str(plans))
    _wait_for_rows(tree, plans, 4)

    tree.reveal(str(plans / "根本没有.md"))
    tree.reveal("")

    assert tree.root() == str(plans)


def test_reveal_plan_uses_the_remembered_path(tree: PlanTree, tmp_path: Path):
    """「回到方案目录」用的是记住的那份方案（`set_plan_path` 不触发信号）。"""
    plans = _folder(tmp_path)
    tree.set_root(str(plans))
    _wait_for_rows(tree, plans, 4)
    chosen: list[str] = []
    tree.plan_chosen.connect(chosen.append)

    tree.set_plan_path(str(plans / "方案二.txt"))
    tree.reveal_plan()
    QTest.qWait(200)

    assert tree.plan_path() == str(plans / "方案二.txt")
    assert tree.model.fileName(tree.view.currentIndex()) == "方案二.txt"
    assert chosen == []          # 只是定位，不该反过来又"选"一次


def test_reveal_plan_without_a_plan_does_nothing(tree: PlanTree, tmp_path: Path):
    """还没选方案时点「回到方案目录」不许炸。"""
    plans = _folder(tmp_path)
    tree.set_root(str(plans))
    _wait_for_rows(tree, plans, 4)

    tree.reveal_plan()          # 不抛异常即通过

    assert tree.root() == str(plans)


# ── 外观契约 ──────────────────────────────────────────────────────────


def test_widget_names_are_contract(tree: PlanTree):
    """控件名即契约：主题 QSS 按名字上样式，改名等于丢样式。"""
    assert tree.objectName() == "planTreeBox"
    assert tree.view.objectName() == "planTree"
    assert tree.root_label.objectName() == "planTreeRoot"
    assert tree.pick_button.objectName() == "planTreePickButton"
    assert tree.reveal_button.objectName() == "planTreeRevealButton"
    assert tree.refresh_button.objectName() == "planTreeRefreshButton"


def test_tree_has_a_minimum_height_and_hides_extra_columns(tree: PlanTree):
    """树太矮等于看不见（左栏是滚动区）；大小/类型/修改时间那几列必须藏起来，
    否则名字会被挤没。"""
    assert tree.view.minimumHeight() == MIN_HEIGHT
    for column in range(1, tree.model.columnCount()):
        assert tree.view.isColumnHidden(column) is True


def test_plan_suffixes_cover_markdown_and_text():
    """后缀表是"点一下就当方案"的判据，别只剩下 .md（.txt 方案很常见）。"""
    assert set(PLAN_SUFFIXES) == {".md", ".markdown", ".txt"}
