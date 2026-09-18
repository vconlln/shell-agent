"""可折叠区块：一个标题行 + 一块内容，点标题收起/展开。

为什么要它：右栏挤了三块（校验报告 / 执行输出 / 模型取舍说明），每块都得有最小高度，
窗口一矮就互相抢空间。折叠让用户自己决定"现在不看哪块"，而不是让布局去猜。

两条约定：
1. **默认展开**（用户裁定）：收起是用户主动做的动作，不是默认状态 —— 默认收起会让
   第一次打开的人以为功能不存在。
2. 折叠状态由外面（窗口）负责持久化，本组件只管显示与发信号：它不知道设置文件在哪，
   也不该知道。
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QSizePolicy, QToolButton, QVBoxLayout, QWidget

_ARROW_COLLAPSED = "▸"
_ARROW_EXPANDED = "▾"


class CollapsibleSection(QWidget):
    """带箭头的可折叠区块。

    `widget` 是内容本体（外部仍按原 objectName 找得到它 —— 折叠只是把它藏起来，
    不从对象树上摘掉，所以 `findChild` 与既有测试不受影响）。
    """

    toggled = Signal(bool)   # True = 已收起

    def __init__(self, title: str, widget: QWidget, *, collapsed: bool = False,
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._title = title
        self._collapsed = collapsed

        self.header = QToolButton()
        self.header.setObjectName("sectionToggle")
        self.header.setText(self._label())
        self.header.setCheckable(True)
        self.header.setChecked(collapsed)
        self.header.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextOnly)
        # 让标题行占满整行宽度并左对齐：只按文字宽度响应的话，右边大片空白点不动，
        # 用起来像"点不到"。
        self.header.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.header.setArrowType(Qt.ArrowType.NoArrow)
        self.header.setStyleSheet("QToolButton#sectionToggle { text-align: left; border: none; }")
        self.header.clicked.connect(self._on_clicked)

        self.content = widget

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(2)
        layout.addWidget(self.header)
        layout.addWidget(widget)
        widget.setVisible(not collapsed)

    # ── 状态 ──────────────────────────────────────────────────────────────
    def is_collapsed(self) -> bool:
        return self._collapsed

    def set_collapsed(self, collapsed: bool) -> None:
        """程序化设置（还原设置时用），不发 toggled 信号，避免"还原"又被当成"用户操作"写回去。"""
        self._collapsed = collapsed
        self.header.setChecked(collapsed)
        self.header.setText(self._label())
        self.content.setVisible(not collapsed)

    def _on_clicked(self) -> None:
        self.set_collapsed(self.header.isChecked())
        self.toggled.emit(self._collapsed)

    def _label(self) -> str:
        arrow = _ARROW_COLLAPSED if self._collapsed else _ARROW_EXPANDED
        return f"{arrow} {self._title}"
