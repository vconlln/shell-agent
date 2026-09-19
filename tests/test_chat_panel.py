"""模型对话面板的布局与会话/模型控件（`ui/chat.py`）。"""

from __future__ import annotations

from tu_shell_agent.ui.chat import ChatPanel


def _panel(qtbot) -> ChatPanel:
    panel = ChatPanel()
    qtbot.addWidget(panel)
    panel.resize(900, 520)
    panel.show()
    return panel


def _top_left(panel, widget) -> tuple[int, int]:
    point = widget.mapTo(panel, widget.rect().topLeft())
    return point.x(), point.y()


def test_model_picker_sits_in_the_bottom_button_row(qtbot):
    """模型选择放在**底部按钮行**的右端（用户要求：跟「发送」那些按钮同一行，不要堆在上面）。

    用几何断言钉住：与发送按钮同一行、在它右侧、且在输入框下方。
    """
    panel = _panel(qtbot)
    send_x, send_y = _top_left(panel, panel.send_button)
    model_x, model_y = _top_left(panel, panel.model_combo)
    input_x, input_y = _top_left(panel, panel.input)
    _session_x, session_y = _top_left(panel, panel.session_combo)

    assert abs(send_y - model_y) <= 4, f"模型下拉与发送按钮不在同一行（{model_y} vs {send_y}）"
    assert model_x > send_x, "模型下拉应当在按钮的右侧"
    assert model_y > input_y, "模型下拉应当在输入框下方，而不是顶部"
    assert session_y < model_y, "会话选择仍留在顶部那一行"


def test_session_row_keeps_only_the_session_controls(qtbot):
    """顶部那行只放会话相关控件：模型不再占一行（把纵向空间还给对话记录）。"""
    panel = _panel(qtbot)
    _x, session_y = _top_left(panel, panel.session_combo)
    _x2, refresh_y = _top_left(panel, panel.refresh_button)
    _x3, new_y = _top_left(panel, panel.new_button)
    assert abs(session_y - refresh_y) <= 4 and abs(session_y - new_y) <= 4

    _x4, model_button_y = _top_left(panel, panel.model_button)
    assert model_button_y > session_y + 20, "「可用模型」按钮不该还在顶部那行"


def test_scrolling_repaints_the_whole_window(qtbot):
    """滚动条一动就要整窗重绘。

    半透明窗口只重绘"滚动露出的那一条"时，旧像素会留在后备存储里 —— 屏幕上是重影
    （用户报的"半透明又成这种重影的了"）。所以把滚动与整窗重绘绑在一起。
    """
    from PySide6.QtCore import QEvent, QObject

    class _Counter(QObject):
        def __init__(self) -> None:
            super().__init__()
            self.paints = 0

        def eventFilter(self, obj, event):  # noqa: N802 - Qt 命名
            if event.type() == QEvent.Type.Paint:
                self.paints += 1
            return False

    from tu_shell_agent.ui.main_window import MainWindow
    from tu_shell_agent.ui.settings import AppSettings

    window = MainWindow(wire_controller=False, settings=AppSettings())
    qtbot.addWidget(window)
    window.resize(1200, 800)
    window.show()
    window.tool_tabs.setCurrentWidget(window.settings_page)

    from PySide6.QtWidgets import QScrollArea

    area = window.settings_page.findChild(QScrollArea)
    bar = area.verticalScrollBar()
    assert bar.maximum() > 0, "设置页内容没超出一屏，这条用例失去意义"

    counter = _Counter()
    window.installEventFilter(counter)
    before = counter.paints
    bar.setValue(min(bar.maximum(), 30))
    for _ in range(20):
        qtbot.wait(5)
    counter.paints = counter.paints
    assert counter.paints > before, "滚动之后窗口没有重绘"


def test_old_blur_setting_migrates_to_acrylic(qtbot, tmp_path):
    """旧设置里的 `blur`（"系统提供"）要归到合成后的「亚克力模糊」，否则界面里选不中。"""
    import json

    from tu_shell_agent.ui.settings import AppSettings

    path = tmp_path / "settings.json"
    path.write_text(json.dumps({"backdrop": "blur"}), encoding="utf-8")
    assert AppSettings.load(path).backdrop == "acrylic"
