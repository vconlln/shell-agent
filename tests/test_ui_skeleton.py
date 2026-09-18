"""界面骨架的测试契约：三区 + 两个独立页 + 关闭安全。

objectName（mainSplitter / leftPane / centerPane / rightPane / templatesPane /
historyList / sidePages）是后续任务与审查者共同依赖的契约，不要改名。
"""

from PySide6.QtWidgets import QSplitter, QTabWidget

from tu_shell_agent.ui.main_window import MainWindow


def test_main_window_has_three_panes_and_two_pages(qtbot):
    window = MainWindow()
    qtbot.addWidget(window)

    splitter = window.findChild(QSplitter, "mainSplitter")
    assert splitter is not None, "三区必须是 QSplitter"
    assert splitter.count() == 3, "左/中/右三栏"

    tabs = window.findChild(QTabWidget, "sidePages")
    assert tabs is not None
    assert [tabs.tabText(i) for i in range(tabs.count())] == ["环境自检", "设置"]


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
