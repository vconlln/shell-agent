"""`WrapRow`：放不下就折行，而不是重叠（用户报过"挤压到看不见"）。

它存在的具体理由：对话面板底部那行控件（发送/取消/把最新脚本放进中栏 + 模型下拉 + 可用模型）
自然宽度约 490px，而右列在窄窗口或用户拖过分割条之后只有 380px 左右。
"""

from __future__ import annotations

from PySide6.QtWidgets import QComboBox, QLabel, QPushButton

from tu_shell_agent.ui.widgets.wrap_row import WrapRow


def _row(qtbot, count: int = 1, gap: int | None = None) -> tuple[WrapRow, list]:
    widgets = [QPushButton(f"按钮{index}") for index in range(count)]
    row = WrapRow(widgets, gap=gap)
    qtbot.addWidget(row)
    row.show()
    return row, widgets


def _name(widget) -> str:
    text = getattr(widget, "text", None)
    return widget.objectName() or (text() if callable(text) else type(widget).__name__)


def _overlaps(widgets) -> list[tuple[str, str]]:
    found = []
    boxes = [(_name(w), w.geometry()) for w in widgets]
    for index in range(len(boxes)):
        for other in range(index + 1, len(boxes)):
            if boxes[index][1].intersects(boxes[other][1]):
                found.append((boxes[index][0], boxes[other][0]))
    return found


def test_wide_row_keeps_everything_on_one_line(qtbot):
    """够宽就是一行 —— 折行只在放不下时发生（宽屏下折行看起来像出了 bug）。"""
    row, widgets = _row(qtbot, 3)
    row.resize(600, 40)
    qtbot.wait(20)

    assert row.row_count() == 1
    assert not _overlaps(widgets)


def test_narrow_row_wraps_instead_of_overlapping(qtbot):
    """放不下就折行；每个控件都必须在自己的行里、不越界、不重叠。

    宽度取"能放下两个按钮、放不下五个"的档位（这是对话面板右列真实的取值范围）。
    """
    row, widgets = _row(qtbot, 5)
    one = sum(widget.sizeHint().width() for widget in widgets) + 6 * 4
    row.resize(int(one * 0.6), 120)
    qtbot.wait(20)

    assert row.row_count() >= 2, "没有折行"
    assert not _overlaps(widgets)
    for widget in widgets:
        assert widget.geometry().right() <= row.width(), f"{widget.text()} 越出行宽"


def test_sizing_uses_the_minimum_footprint_not_the_size_hint(qtbot):
    """折行判定必须按"最小占位"算，不能只看 `sizeHint`。

    空的 `QComboBox` 的 sizeHint 只有 38px，但 `setMinimumWidth(200)` 之后布局**压不到**
    200 以下 —— 只看 sizeHint 会得出"一行放得下"，排出来却越出右边缘（这就是
    "算得下、排出来溢出"的来源）。
    """
    combo = QComboBox()
    combo.setMinimumWidth(200)
    button = QPushButton("可用模型")
    row = WrapRow([QLabel("模型"), combo, button], spacing=6)
    qtbot.addWidget(row)
    row.show()
    row.resize(240, 80)
    qtbot.wait(20)

    assert not _overlaps([combo, button])
    for widget in (combo, button):
        assert widget.geometry().right() <= row.width(), f"{widget.objectName() or widget} 越出行宽"


def _needed(row: WrapRow) -> int:
    """一行放得下这些控件需要多宽（按最小占位算，理由同下面那条用例）。"""
    widths = [
        max(w.sizeHint().width(), w.minimumSizeHint().width(), w.minimumWidth())
        for w in row._widgets
    ]
    return sum(widths) + 6 * (len(widths) - 1)


def test_group_split_keeps_a_label_with_its_control(qtbot):
    """`gap` 分组：宽时右组靠右；折行时**整组**折，别把标签留在上一行末尾。

    宽度按控件实际需要的宽度算，不写死像素：按钮宽度跟着全局字体走，而同一会话里
    别的用例会改字号（写死 260 在全量跑时曾经"一行就放下了"而假红）。
    """
    label = QLabel("模型")
    combo = QComboBox()
    combo.setMinimumWidth(110)
    button = QPushButton("可用模型")
    row = WrapRow([QPushButton("发送"), label, combo, button], gap=1, spacing=6)
    qtbot.addWidget(row)
    row.show()
    needed = _needed(row)

    row.resize(needed + 120, 40)
    qtbot.wait(20)
    assert row.row_count() == 1
    assert combo.geometry().x() > row.width() // 2, "宽的时候右组应当贴右端"

    row.resize(needed - 20, 80)
    qtbot.wait(20)
    assert row.row_count() >= 2
    assert row.row_of(label) == row.row_of(combo), "标签与它的下拉被拆到两行了"
    assert not _overlaps([label, combo, button])
