"""可折叠区块：一个标题行 + 一块内容，**按可用空间自动**收起/展开。

为什么要它：右栏常驻三块（校验报告 / 执行输出 / 模型取舍说明与假设），每块都有能用的
最小高度。窗口或分割条一压，三块就会互相抢空间（实测被压到 12~35px，等于看不见）。

**为什么是自动的**（用户裁定，2026-09-19）：折叠不该要用户手动去点 —— 空间够就不折叠，
不够才收，收回来的空间给更需要看的那块。用户不该为了看一眼输出先去点一下标题。

优先级固定为：**校验报告 > 执行输出 > 模型取舍说明与假设**。
理由是运行过程中人盯着的是"有没有问题、跑成什么样"；取舍说明是**事后**核对方案约束时才看的，
所以空间不足时它先让位。校验报告任何时候都不自动收起 —— 它是这一屏的主要结论。

标题行只显示状态、不接收点击（可点的标题会让人以为"这里要手动操作"）。
"""

from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QLabel, QVBoxLayout, QWidget

_ARROW_COLLAPSED = "▸"
_ARROW_EXPANDED = "▾"

# 判定时的缓冲带：正好卡在边界上时不要来回抖（拖分割条时高度常常只差一两像素）
_HYSTERESIS_PX = 16


def plan_collapse(
    available: int,
    minimums: list[tuple[str, int]],
    *,
    keep_expanded: str | None = None,
) -> set[str]:
    """算出当前可用高度下应该收起哪些区块。

    `minimums` 按**优先级从高到低**给出 `(key, 该块能用的最小高度)`；`keep_expanded` 是
    永不收起的那个 key（校验报告）。规则：从优先级最低的开始收，直到剩下的装得下。
    """
    total = sum(height for _key, height in minimums)
    if available >= total + _HYSTERESIS_PX:
        return set()

    collapsed: set[str] = set()
    remaining = total
    for key, height in reversed(minimums):      # 末尾 = 优先级最低
        if remaining + _HYSTERESIS_PX <= available:
            break
        if key == keep_expanded:
            continue
        collapsed.add(key)
        remaining -= height
    return collapsed


class CollapsibleSection(QWidget):
    """可自动折叠的区块。

    `widget` 是内容本体：折叠只是把它 `setVisible(False)`，不从对象树上摘掉，
    所以 `findChild` 与既有测试、控制器代码都不受影响。
    """

    auto_state_changed = Signal(bool)   # True = 因空间不足被自动收起

    def __init__(self, title: str, widget: QWidget, *, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._title = title
        self._collapsed = False

        self.header = QLabel(self._label())
        self.header.setObjectName("sectionHeader")
        self.header.setProperty("role", "section")

        self.content = widget

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(2)
        layout.addWidget(self.header)
        layout.addWidget(widget)
        self._refresh_tooltip()

    # ── 状态 ──────────────────────────────────────────────────────────────
    def is_collapsed(self) -> bool:
        return self._collapsed

    def set_collapsed(self, collapsed: bool, *, emit: bool = False) -> None:
        """设置折叠状态。`emit=True` 只给"自动判定"用（用于界面提示与测试观察）。"""
        if collapsed == self._collapsed:
            return
        self._collapsed = collapsed
        self.header.setText(self._label())
        self.content.setVisible(not collapsed)
        self._refresh_tooltip()
        if emit:
            self.auto_state_changed.emit(collapsed)

    def _label(self) -> str:
        arrow = _ARROW_COLLAPSED if self._collapsed else _ARROW_EXPANDED
        return f"{arrow} {self._title}"

    def _refresh_tooltip(self) -> None:
        if self._collapsed:
            self.header.setToolTip(f"{self._title}：空间不足已自动收起，把面板拉高即可展开")
        else:
            self.header.setToolTip(f"{self._title}：空间不足时会自动收起")
