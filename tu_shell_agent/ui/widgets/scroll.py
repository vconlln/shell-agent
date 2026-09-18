"""把"表单型"面板包进滚动区域：空间不够时滚动，而不是把控件压扁。

为什么需要它（用户报出来的真实缺陷）：左栏内容需要 576px（最小 474），实际只分到 383px，
于是 QFormLayout 把输入控件压到 **13px 高**（sizeHint 是 29px）。连带的两个症状：
- 控件小到几乎点不中、字也看不清；
- 13px 高时圆角半径 10px ≥ 半高 6.5px，**Qt 会整个退回画直角** —— 于是用户看到
  "按钮是圆角，但底色还是长方形"（其实是被压扁导致的）。

结论：表单类面板不该靠"压缩控件"来适应高度，该靠滚动。
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QFrame, QScrollArea, QVBoxLayout, QWidget


def scrollable(content: QWidget, *, object_name: str = "") -> QScrollArea:
    """把 content 包成"可滚动面板"：内容按自身尺寸显示，放不下就出滚动条。

    三个细节都是必须的：
    - `setWidgetResizable(True)`：让内容跟随宽度（否则会横向出现滚动条、宽度按 sizeHint 定死）；
    - 无边框 + 透明背景：滚动区自己不该画任何东西，否则会在圆角卡片里出现一个方形色块；
    - 水平滚动条按需：表单通常在窄的时候靠换行，不横滚。
    """
    area = QScrollArea()
    if object_name:
        area.setObjectName(object_name)
    area.setWidgetResizable(True)
    area.setFrameShape(QFrame.Shape.NoFrame)
    area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
    area.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
    area.setStyleSheet("QScrollArea { background: transparent; border: none; }")
    area.viewport().setStyleSheet("background: transparent;")
    area.setWidget(content)
    return area


def form_container() -> tuple[QWidget, QVBoxLayout]:
    """建一个"内容容器 + 它的纵向布局"，供面板把自己的控件塞进去。

    用法：`content, layout = form_container()`，把控件加进 layout，
    最后 `outer.addWidget(scrollable(content))`。
    """
    content = QWidget()
    content.setObjectName("scrollContent")
    layout = QVBoxLayout(content)
    layout.setContentsMargins(0, 0, 0, 0)
    return content, layout
