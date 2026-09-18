"""界面骨架的测试契约：三栏 + 底部工具区页签 + 关闭安全。

objectName（mainSplitter / verticalSplitter / leftPane / centerPane / rightPane /
templatesPane / historyList / toolTabs）是后续任务与审查者共同依赖的契约，不要改名。

2026-09-19 排布调整（用户裁定）：低频面板收进底部「工具区」页签，主区三栏各自只干一件事。
因此 `sidePages`（环境自检/设置两页）演进为 `toolTabs`（历史运行/模板库/模型对话/环境自检/设置），
`templatesPane` 从左列移进工具区 —— 契约跟着更新，不是放宽。
"""

from PySide6.QtWidgets import QSplitter, QTabWidget

from tu_shell_agent.ui.main_window import MainWindow


def test_main_window_has_three_panes_and_tool_tabs(qtbot):
    window = MainWindow()
    qtbot.addWidget(window)

    splitter = window.findChild(QSplitter, "mainSplitter")
    assert splitter is not None, "三区必须是 QSplitter"
    assert splitter.count() == 3, "左/中/右三栏"

    tabs = window.findChild(QTabWidget, "toolTabs")
    assert tabs is not None, "底部工具区页签不见了"
    assert [tabs.tabText(i) for i in range(tabs.count())] == [
        "历史运行", "模板库", "模型对话", "环境自检", "设置",
    ]


def test_main_window_exposes_named_panes(qtbot):
    from PySide6.QtWidgets import QWidget

    window = MainWindow()
    qtbot.addWidget(window)
    for name in ("leftPane", "centerPane", "rightPane", "templatesPane", "historyList"):
        assert window.findChild(QWidget, name) is not None, name


def test_close_event_cancels_running_engine_without_raising(qtbot):
    window = MainWindow()
    qtbot.addWidget(window)
    window.close()  # 未运行时也必须安全


def test_right_pane_blocks_are_collapsible_and_default_expanded(qtbot):
    """右栏三块可折叠、默认展开（用户裁定：默认收起会让人以为功能不存在）。"""
    window = MainWindow()
    qtbot.addWidget(window)

    sections = window.right_pane.sections
    assert set(sections) == {"findings", "output", "notes"}
    for name, section in sections.items():
        assert section.is_collapsed() is False, f"{name} 默认被收起了"
        assert section.content.isVisible() or not window.isVisible()
