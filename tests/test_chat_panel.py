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
