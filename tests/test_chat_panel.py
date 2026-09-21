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
    window.open_console(window.settings_page)      # 设置页搬进控制台弹窗了

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


# ── 窄栏里折行而不是重叠（用户报过"挤压到看不见"）──────────────────────


def _overlaps(panel, widgets) -> list[tuple[str, str]]:
    found = []
    boxes = [(w.objectName() or w.text(), w.geometry()) for w in widgets]
    for index in range(len(boxes)):
        for other in range(index + 1, len(boxes)):
            if boxes[index][1].intersects(boxes[other][1]):
                found.append((boxes[index][0], boxes[other][0]))
    return found


def _controls(panel):
    return [panel.send_button, panel.cancel_button, panel.extract_button,
            panel.model_label, panel.model_combo, panel.model_button]


def test_controls_stay_on_one_row_when_the_column_is_wide(qtbot):
    """栏够宽时仍然是一行：模型选择与发送按钮同一行、且在右端（用户明确要求过）。"""
    panel = _panel(qtbot)
    panel.resize(560, 520)
    qtbot.wait(20)

    assert panel.controls_row.row_count() == 1
    send_y = _top_left(panel, panel.send_button)[1]
    for widget in _controls(panel):
        assert abs(_top_left(panel, widget)[1] - send_y) <= 4, "控件不在同一行"
    assert _top_left(panel, panel.model_combo)[0] > _top_left(panel, panel.extract_button)[0]
    assert not _overlaps(panel, _controls(panel))


def test_controls_wrap_instead_of_overlapping_when_narrow(qtbot):
    """栏被压窄时折行、**不许重叠**（实测修前：右列 386px 时模型下拉盖住「可用模型」）。

    真实触发路径是"窗口缩到最小 / 分割条往右拖"，这里直接量面板宽度，等价且更快。
    """
    panel = _panel(qtbot)
    panel.resize(380, 520)
    qtbot.wait(20)

    assert panel.controls_row.row_count() >= 2, "窄栏里没有折行"
    assert not _overlaps(panel, _controls(panel)), "折行之后仍然重叠"
    for widget in _controls(panel):
        geometry = widget.geometry()
        assert geometry.right() <= panel.width(), f"{widget.objectName()} 越出面板右边缘"
        assert geometry.x() >= 0

    # 整组折行：模型标签与它的下拉留在同一行（别把标签留在上一行末尾）
    assert panel.controls_row.row_of(panel.model_label) == panel.controls_row.row_of(panel.model_combo)
