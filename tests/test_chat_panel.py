"""模型对话面板的布局与会话/模型控件（`ui/chat.py`）。"""

from __future__ import annotations

from tu_shell_agent.ui.chat import ChatPanel


def _panel(qtbot, app) -> ChatPanel:
    """装好主题的面板。

    必须装主题：按钮的宽度由 QSS 的 padding/min-height 决定 —— 不装主题时
    `QPushButton.sizeHint()` 是 Qt 的默认值 80px，底栏那一行需要 388px 就会折行，
    而生产环境里（主题在启动时装好）同样一行只需要 330px。用例要测的是用户看到的那一版。
    """
    from tu_shell_agent.ui.theme import apply_theme

    apply_theme(app)
    panel = ChatPanel()
    qtbot.addWidget(panel)
    panel.resize(900, 520)
    panel.show()
    return panel


def _top_left(panel, widget) -> tuple[int, int]:
    point = widget.mapTo(panel, widget.rect().topLeft())
    return point.x(), point.y()


def test_model_picker_sits_in_the_bottom_button_row(qtbot, restore_app):
    """模型选择在**输入卡片的下沿**、紧挨发送按钮（用户给的参照图形状）。

    用户第二次点名这条（"把这个模型移动到跟发送在同一行"），所以这里按**面板最小宽度下的
    真实几何**断言"同一行"，而不是只看宽面板。
    """
    panel = _panel(qtbot, restore_app)
    panel.resize(panel.minimumSizeHint().width(), 520)     # 右列在窄窗口里的实际宽度
    qtbot.wait(20)
    send_x, send_y = _top_left(panel, panel.send_button)
    model_x, model_y = _top_left(panel, panel.model_combo)
    input_x, input_y = _top_left(panel, panel.input)
    mode_x, _mode_y = _top_left(panel, panel.mode_button)
    _session_x, session_y = _top_left(panel, panel.session_combo)

    assert abs(send_y - model_y) <= 4, f"模型下拉与发送按钮不在同一行（{model_y} vs {send_y}）"
    assert send_x > model_x, "发送按钮在模型选择的右侧（参照图里它是最右那颗圆点）"
    assert model_y > input_y, "模型下拉应当在输入框下方，而不是顶部"
    assert mode_x < model_x, "模式胶囊在左侧，模型在右侧"
    assert session_y < model_y, "会话选择仍留在顶部那一行"
    assert panel.controls_row.row_count() == 1, (
        "面板最小宽度下底部控件被折成了两行 —— 用户要的是模型跟发送同一行"
    )


def test_send_and_cancel_are_round_buttons_inside_the_composer(qtbot, restore_app):
    """发送/停止是**圆形**按钮、长在输入卡片里（参照图的形状），停止只在忙时出现。"""
    panel = _panel(qtbot, restore_app)
    qtbot.wait(20)

    assert panel.send_button.text() == "↑", "发送按钮不是那颗圆点（参照图里是一个上箭头）"
    assert panel.composer.isAncestorOf(panel.send_button), "发送按钮不在输入卡片里"
    assert panel.composer.isAncestorOf(panel.input), "输入框不在输入卡片里"
    # 方形＝半径没生效（Qt 里半径 >= 高度一半时退回直角，这里正好取一半 → 圆）
    assert panel.send_button.width() == panel.send_button.height(), "发送按钮不是正方形，画不成圆"
    assert not panel.cancel_button.isVisible(), "空闲时不该出现停止按钮"
    panel.set_busy(True)
    assert panel.cancel_button.isVisible(), "忙的时候必须能看到停止按钮"
    panel.set_busy(False)


def test_enter_sends_and_ctrl_enter_breaks_the_line(qtbot, restore_app):
    """Enter 发送、Ctrl+Enter 换行（用户 2026-09-20 明确要求）。

    改之前是反的。这条必须钉在**输入框**上：`QPlainTextEdit` 自己会吃掉回车，
    父控件的 keyPressEvent 收不到 —— 所以"发得出去"与"换得成行"两件事都要在这里验证。
    """
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QKeyEvent
    from PySide6.QtWidgets import QApplication

    panel = _panel(qtbot, restore_app)
    sent: list[str] = []
    panel.send_requested.connect(sent.append)
    panel.input.setFocus()
    panel.input.setPlainText("第一行")
    # 光标挪到末尾（真实打字就在这里）；不挪的话插入点在第 0 位，换行会插到行首
    from PySide6.QtGui import QTextCursor

    panel.input.moveCursor(QTextCursor.MoveOperation.End)

    def press(key, modifiers):
        event = QKeyEvent(QKeyEvent.Type.KeyPress, key, modifiers)
        QApplication.sendEvent(panel.input, event)

    press(Qt.Key.Key_Return, Qt.KeyboardModifier.ControlModifier)
    assert panel.input.toPlainText() == "第一行\n", "Ctrl+Enter 没有换行"
    assert sent == [], "Ctrl+Enter 不该把消息发出去"

    panel.input.setPlainText("第二行")
    press(Qt.Key.Key_Return, Qt.KeyboardModifier.NoModifier)
    assert sent == ["第二行"], f"单独 Enter 没有发送：{sent}"
    assert panel.input.toPlainText() == "", "发送之后输入框要清空"


def test_session_row_keeps_only_the_session_controls(qtbot, restore_app):
    """顶部那行只放会话相关控件：模型不再占一行（把纵向空间还给对话记录）。"""
    panel = _panel(qtbot, restore_app)
    _x, session_y = _top_left(panel, panel.session_combo)
    _x2, refresh_y = _top_left(panel, panel.refresh_button)
    _x3, new_y = _top_left(panel, panel.new_button)
    assert abs(session_y - refresh_y) <= 4 and abs(session_y - new_y) <= 4

    _x4, model_y = _top_left(panel, panel.model_combo)
    assert model_y > session_y + 20, "模型下拉不该还在顶部那行"


def test_scrolling_repaints_the_whole_window(qtbot, restore_app):
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
    """输入卡片下沿那一行上的控件（顺序即左右顺序）。"""
    return [panel.plus_button, panel.mode_button, panel.model_combo, panel.send_button]


def test_controls_stay_on_one_row_when_the_column_is_wide(qtbot, restore_app):
    """栏够宽时仍然是一行：＋ / 模式胶囊在左，模型与发送在右（用户明确要求过）。"""
    panel = _panel(qtbot, restore_app)
    panel.resize(560, 520)
    qtbot.wait(20)

    assert panel.controls_row.row_count() == 1
    send_y = _top_left(panel, panel.send_button)[1]
    for widget in _controls(panel):
        assert abs(_top_left(panel, widget)[1] - send_y) <= 4, "控件不在同一行"
    assert _top_left(panel, panel.model_combo)[0] > _top_left(panel, panel.mode_button)[0]
    assert not _overlaps(panel, _controls(panel))


def test_controls_wrap_instead_of_overlapping_when_narrow(qtbot, restore_app):
    """栏被压到**比面板最小宽度还窄**时折行、不许重叠。

    现在底栏那一行（发送/取消/存入中栏 + 模型下拉）只需要 330px，而面板最小宽度是 385 ——
    正常窗口里永远不会折行（这正是用户要的"模型跟发送同一行"）。折行是**兜底**：
    窗口被窗口管理器压到比最小尺寸还小时（高 DPI 小屏），Qt 只能违反最小尺寸，
    那时候必须是折行而不是重叠（实测修前：右列 386px 时模型下拉盖住「可用模型」按钮）。
    """
    panel = _panel(qtbot, restore_app)
    panel.setMinimumWidth(260)          # 模拟"布局被违反"：比任何正常窗口都窄
    panel.resize(260, 520)
    qtbot.wait(20)

    assert panel.controls_row.row_count() >= 2, "窄栏里没有折行"
    assert not _overlaps(panel, _controls(panel)), "折行之后仍然重叠"
    for widget in _controls(panel):
        geometry = widget.geometry()
        assert geometry.right() <= panel.width(), f"{widget.objectName()} 越出面板右边缘"
        assert geometry.x() >= 0

    # 整组折行：左侧（＋ / 模式胶囊）与右侧（模型 / 发送）各自成组，不会被拆开混排
    assert panel.controls_row.row_of(panel.plus_button) == panel.controls_row.row_of(panel.mode_button)
    assert panel.controls_row.row_of(panel.model_combo) == panel.controls_row.row_of(panel.send_button)
    assert panel.controls_row.row_of(panel.plus_button) != panel.controls_row.row_of(panel.model_combo)


# ── 可用模型列表：点开下拉就现取现列（用户要求"可用模型点开用列表呈现"）────


def test_opening_the_model_dropdown_asks_for_the_model_list(qtbot, restore_app):
    """点开模型下拉＝"我要挑模型"：这时候要一次可用模型列表（后端可能是起子进程取的）。"""
    panel = _panel(qtbot, restore_app)
    asked: list[str] = []
    panel.models_requested.connect(lambda: asked.append("ask"))

    panel.model_combo.showPopup()
    qtbot.wait(20)

    assert asked == ["ask"], "点开下拉没有去要可用模型列表"
    panel.model_combo.hidePopup()


def test_model_list_is_offered_in_the_dropdown_and_sets_the_model(qtbot, restore_app):
    """列出来的模型可以直接选：选中即生效（发 model_changed），当前模型不会被刷新弄丢。"""
    panel = _panel(qtbot, restore_app)
    panel.set_model("gpt-5.3")
    panel.set_models(["gpt-5.3", "glm-5.3", "opus"])

    items = [panel.model_combo.itemText(i) for i in range(panel.model_combo.count())]
    assert items[0] == "", "第一项是空（用会话模型），与占位文字一致"
    assert "glm-5.3" in items and "opus" in items
    assert items[-1] == "重新获取可用模型", f"末项应当是重新获取：{items}"
    assert panel.selected_model() == "gpt-5.3", "刷新列表把已选模型弄丢了"

    chosen: list[str] = []
    panel.model_changed.connect(chosen.append)
    panel.model_combo.setCurrentIndex(panel.model_combo.findText("opus"))
    assert chosen == ["opus"]
    assert panel.selected_model() == "opus"


def test_refresh_item_asks_again_without_becoming_the_model(qtbot, restore_app):
    """"重新获取可用模型"不是一个模型名：选中它只重新取列表，**不许**当成模型发出去。"""
    panel = _panel(qtbot, restore_app)
    panel.set_models(["gpt-5.3", "opus"])
    panel.set_model("opus")

    asked: list[str] = []
    chosen: list[str] = []
    panel.models_requested.connect(lambda: asked.append("ask"))
    panel.model_changed.connect(chosen.append)

    index = panel.model_combo.findText("重新获取可用模型")
    panel.model_combo.setCurrentIndex(index)

    assert asked == ["ask"], "选中「重新获取可用模型」没有去重新取列表"
    assert chosen == [], f"把「重新获取可用模型」当成模型发出去了：{chosen}"
    assert panel.selected_model() == "opus", "重新获取之后应当保持原来选的模型"


def test_model_can_still_be_typed_by_hand(qtbot, restore_app):
    """CLI 后端常要手输完整模型名：下拉可编辑这一条不能丢。"""
    panel = _panel(qtbot, restore_app)
    panel.set_models(["a", "b"])

    chosen: list[str] = []
    panel.model_changed.connect(chosen.append)
    panel.model_combo.setEditText("deepseek/deepseek-v4-pro")

    assert panel.selected_model() == "deepseek/deepseek-v4-pro"
    assert chosen == ["deepseek/deepseek-v4-pro"]


def test_opening_the_dropdown_twice_does_not_hammer_the_backend(qtbot, restore_app):
    """连点两下下拉不该跑两次取列表（openccode 那边是起子进程），想强制刷新有末项。"""
    panel = _panel(qtbot, restore_app)
    asked: list[str] = []
    panel.models_requested.connect(lambda: asked.append("ask"))

    panel.model_combo.showPopup()
    qtbot.wait(10)
    panel.set_models(["gpt-5.3"])          # 模拟列表回来了
    panel.model_combo.hidePopup()
    panel.model_combo.showPopup()
    qtbot.wait(10)
    panel.model_combo.hidePopup()

    assert asked == ["ask"], f"同一分钟内点开两次要了 {len(asked)} 次列表"


def test_model_popup_is_wide_enough_to_read_full_names(qtbot, restore_app):
    """弹层要放得下完整的模型名。

    实测（修前）：弹层宽度跟下拉一样是 130px，`deepseek/deepseek-v4-flash` 在列表里被截成
    `deepseek/deepse…` —— 而"选哪个模型"恰恰要看清全名。
    """
    from PySide6.QtGui import QFontMetrics

    panel = _panel(qtbot, restore_app)
    name = "deepseek/deepseek-v4-flash"
    panel.set_models([name, "opus"])

    view = panel.model_combo.view()
    needed = QFontMetrics(view.font()).horizontalAdvance(name)
    assert view.minimumWidth() >= needed, (
        f"弹层最小宽度 {view.minimumWidth()}px 放不下模型名（需要 {needed}px）"
    )
