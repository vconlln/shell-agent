"""布局可拖拽性与尺寸记忆（用户报过"不能调节竖向的长度"）。

那次事故的根因有两层，两层都要有测试守着：
1. 主题把 `QSplitter::handle` 的宽/高写成了 1px —— 视觉上是细线，但**鼠标抓不住**；
2. 上下两块（三栏区 / 历史与设置区）当时是 QVBoxLayout 里的固定 1:1，根本不是分割器，
   无论怎么拖都不可能改比例。
"""

from __future__ import annotations

import json

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QSplitter

from tu_shell_agent.ui.main_window import MainWindow
from tu_shell_agent.ui.settings import AppSettings


@pytest.fixture
def window(qtbot, tmp_path):
    win = MainWindow(
        wire_controller=False,
        settings=AppSettings(run_root=str(tmp_path / "runs"), templates_dir=str(tmp_path / "tpl")),
    )
    qtbot.addWidget(win)
    win.resize(1440, 900)
    win.show()
    qtbot.waitExposed(win)
    return win


def test_all_splitters_have_a_grabbable_handle(window):
    """分割条的可抓宽度必须够（1px 的细线抓不住，这是用户实际遇到的问题）。"""
    splitters = window.findChildren(QSplitter)
    assert splitters, "一个分割器都没有？"
    for splitter in splitters:
        assert splitter.handleWidth() >= 6, f"{splitter.objectName()} 的把手只有 {splitter.handleWidth()}px，抓不住"


def test_top_and_bottom_areas_can_be_resized_vertically(window):
    """三栏区与下方"历史+设置"区之间必须能改高度比例。

    原实现是 QVBoxLayout 的 addWidget(1) + addLayout(1)：比例写死 1:1，用户无法调整。
    """
    vertical = window.findChild(QSplitter, "verticalSplitter")
    assert vertical is not None, "上下两块之间没有分割器 → 竖向比例不可调"
    assert vertical.orientation() == Qt.Orientation.Vertical
    assert vertical.count() == 2

    before = vertical.sizes()
    assert before[0] > 0 and before[1] > 0
    # 模拟拖动：往"上面小、下面大"拖。注意分割器会尊重子控件的最小高度，所以这里断言
    # "确实动得动、且方向正确"，而不是断言等于我传入的数字。
    vertical.setSizes([400, 600])
    after = vertical.sizes()
    assert after[0] < before[0] - 40, f"上部压不下去：{before} → {after}"
    assert after[1] > before[1] + 40, f"下部顶不上来：{before} → {after}"


def test_resized_layout_is_remembered_in_settings(window, qtbot):
    """拖过的尺寸要写进设置并在下次开窗时还原（否则每次都得重拖）。"""
    window.vertical_splitter.setSizes([380, 620])
    window._save_layout()

    saved = json.loads(window.settings.layout)
    assert len(saved["vertical"]) == 2
    assert all(isinstance(value, int) and value > 0 for value in saved["vertical"])

    # 契约是"存什么还原什么"（不是"拖出来的比例一定如何"：分割器会尊重子控件最小高度，
    # 夹取后的结果才是真相，所以拿夹取后的实际尺寸去比）
    actual = window._splitter_state()["vertical"]
    again = MainWindow(wire_controller=False, settings=window.settings)
    qtbot.addWidget(again)
    again.resize(1440, 900)
    again.show()
    qtbot.waitExposed(again)          # 还原发生在 showEvent 里，要等它显示出来
    assert again.vertical_splitter.sizes() == actual, "存进去的尺寸没有还原"


def test_corrupt_layout_setting_is_ignored_not_fatal(window):
    """设置里的布局串坏掉时用默认比例，不能让界面起不来。"""
    window.settings.layout = "{不是合法 JSON"
    window._restore_layout()          # 不抛异常即可
    window.settings.layout = json.dumps({"main": [1, 2]})   # 数量对不上
    window._restore_layout()


def test_history_and_side_pages_are_resizable(window):
    """历史列表与"环境自检/设置"之间也要能拖（原来是写死的 1:2）。"""
    bottom = window.findChild(QSplitter, "bottomSplitter")
    assert bottom is not None
    assert bottom.count() == 2
    before = bottom.sizes()
    bottom.setSizes([220, 880])
    assert bottom.sizes() != before


def test_handle_width_does_not_depend_on_the_stylesheet(qtbot, tmp_path):
    """把手宽度必须在代码里设死，不能只靠 QSS。

    实测：QSS 的 `QSplitter::handle { width }` 确实会影响它，但**样式表没加载时**会退回
    Qt 默认的 4px，而 4px 抓不住。把一个"能不能用鼠标操作"的属性交给样式表是脆的。
    """
    plain = MainWindow(
        wire_controller=False,
        settings=AppSettings(run_root=str(tmp_path / "r"), templates_dir=str(tmp_path / "t")),
    )
    qtbot.addWidget(plain)          # 故意不装主题（不调 apply_theme）
    for splitter in plain.findChildren(QSplitter):
        assert splitter.handleWidth() >= 6, splitter.objectName()
