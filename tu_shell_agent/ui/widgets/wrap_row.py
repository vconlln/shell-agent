"""放不下就折行的横向排布（Qt 没有内置的流式布局）。

为什么需要它：对话面板底部那行控件（发送 / 取消 / 把最新脚本放进中栏 + 模型下拉 + 可用模型）
合计约 520px。右列在高 DPI 的小屏、或用户把三栏分割条往右拖之后会被压到 380~400px，
而 `QHBoxLayout` 只有两种反应：把控件**重叠**，或者把窗口的最小宽度顶上去 ——
前者正是用户报过的"输入框盖在记录区上"那一类挤压问题（实测：窗口 960 宽时右列 386px，
模型下拉与「可用模型」按钮重叠）。

所以这里在放不下时折成两行：宽的时候仍然是**一行**（用户要求模型选择与发送按钮同一行、
且在右端），窄的时候折行而不是重叠。
"""

from __future__ import annotations

from PySide6.QtCore import QSize
from PySide6.QtWidgets import QHBoxLayout, QVBoxLayout, QWidget

# 最多折成三行：右列被压到 230px 左右（比设计下限还窄）时两行也放不下，
# 三行能让每个控件都还在自己的位置上 —— 挤到这个程度本来就不好用，但**不许重叠**。
_MAX_ROWS = 3


class WrapRow(QWidget):
    """把一串控件排成一行；宽度不够时从 `gap` 处折到第二行。

    `gap` 是"左组/右组"的分界下标：左组靠左、右组靠右（中间用伸缩项顶开）；
    折行之后两行各自靠左排。传 None 表示整行靠左。
    """

    def __init__(
        self,
        widgets: list[QWidget],
        *,
        gap: int | None = None,
        spacing: int = 6,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._widgets = list(widgets)
        self._gap = gap
        self._spacing = spacing
        self._rows = [QHBoxLayout() for _ in range(_MAX_ROWS)]
        for row in self._rows:
            row.setContentsMargins(0, 0, 0, 0)
            row.setSpacing(spacing)
        self._outer = QVBoxLayout(self)
        self._outer.setContentsMargins(0, 0, 0, 0)
        self._outer.setSpacing(spacing)
        # 不让布局把"当前这一行"的宽度写成这个控件的最小宽度：Qt 的默认约束
        # （SetDefaultConstraint）会把布局的最小尺寸设成控件的最小尺寸，于是**折行永远
        # 不会发生**（实测：控件怎么缩都停在 312px 不动）。折行的判定归 `_split` 管。
        self._outer.setSizeConstraint(QVBoxLayout.SizeConstraint.SetNoConstraint)
        for row in self._rows:
            self._outer.addLayout(row)
        self._assignment: tuple[tuple[int, ...], ...] | None = None
        # 先按"一行放得下"布局：真实宽度要等父级排完才知道
        self._apply(self._split(10**6))

    # ── 对外 ──────────────────────────────────────────────────────
    def row_of(self, widget: QWidget) -> int:
        """控件当前在第几行（0/1）——测试与调试用。"""
        for index, row in enumerate(self._rows):
            for position in range(row.count()):
                item = row.itemAt(position)
                if item is not None and item.widget() is widget:
                    return index
        return -1

    def row_count(self) -> int:
        """当前真的用到的行数。"""
        return max(1, sum(1 for row in self._rows if row.count() > 0))

    # ── Qt 钩子 ───────────────────────────────────────────────────
    def resizeEvent(self, event) -> None:  # noqa: N802 - Qt 命名
        super().resizeEvent(event)
        self._relayout(self.width())

    def sizeHint(self) -> QSize:  # noqa: N802 - Qt 命名
        """宽度取各控件的自然宽度之和（父级据此决定给多少），高度按当前行数算。"""
        natural = sum(self._footprint(widget) for widget in self._widgets)
        natural += self._spacing * max(len(self._widgets) - 1, 0)
        return QSize(natural, super().sizeHint().height())

    def minimumSizeHint(self) -> QSize:  # noqa: N802 - Qt 命名
        """最小宽度按**最宽的那个控件**算，不按"当前那一行"算。

        必须自己算：内部是"若干行 QHBoxLayout"的竖直堆叠，Qt 报告的最小宽度就是**当前那一行**
        各个控件最小宽度之和 —— 于是"栏比一行还窄"这件事永远不会发生，折行也永远不会触发
        （实测：对话面板的最小宽度被顶到 513px，怎么缩都还挤在一行里，然后就是重叠）。
        """
        widest = max((self._footprint(widget) for widget in self._widgets), default=0)
        return QSize(widest, super().minimumSizeHint().height())

    @staticmethod
    def _footprint(widget: QWidget) -> int:
        """控件在一行里**至少**要占的宽度。

        不能只看 `sizeHint()`：`QComboBox` 的 sizeHint 按内容算（空的时候只有 38px），
        但 `setMinimumWidth(130)` 之后布局根本压不到 130 以下 —— 第一版按 sizeHint 折行，
        结果"算出来放得下、排出来溢出"（实测窗口 960 宽时模型下拉越出面板右边缘）。
        """
        return max(
            widget.sizeHint().width(),
            widget.minimumSizeHint().width(),
            widget.minimumWidth(),
        )

    # ── 内部 ──────────────────────────────────────────────────────
    def _relayout(self, available: int) -> None:
        self._apply(self._split(available))

    def _split(self, available: int) -> tuple[tuple[int, ...], ...]:
        """贪心折行：宽度不够就把断点往 `gap` 处挪，挪不动就把最后一个控件挤到下一行。"""
        widths = [self._footprint(widget) for widget in self._widgets]
        rows: list[list[int]] = [[]]
        used = 0
        for index, width in enumerate(widths):
            extra = width + (self._spacing if rows[-1] else 0)
            if rows[-1] and used + extra > available:
                if len(rows) < _MAX_ROWS:
                    rows.append([index])
                    used = width
                    continue
            rows[-1].append(index)
            used += extra
        # 分组只是同一行里插一个伸缩项（`_apply` 负责），**不能**在这里拆成两行 ——
        # 那会让宽屏下也折行（第一版就是这么错的：一行明明放得下，模型选择还是掉到第二行）。
        if len(rows) > 1 and self._gap is not None:
            left = list(range(self._gap))
            right = list(range(self._gap, len(widths)))
            if self._group_width(left, widths) <= available and (
                self._group_width(right, widths) <= available
            ):
                # 折行时整组折：别把「模型」标签留在第一行末尾、把它的下拉推到第二行
                # （实测那个样子像两个不相干的控件）。
                rows = [left, right]
        return tuple(tuple(row) for row in rows if row)

    def _group_width(self, indices: list[int], widths: list[int]) -> int:
        if not indices:
            return 0
        return sum(widths[index] for index in indices) + self._spacing * (len(indices) - 1)

    def _apply(self, rows: tuple[tuple[int, ...], ...]) -> None:
        if rows == self._assignment:
            return
        self._assignment = rows
        for layout in self._rows:
            # takeAt 之后 item 的所有权归调用方（Qt 文档）；控件不归 item 所有，
            # 所以不调 setParent(None) —— 那会让控件瞬间隐藏并丢掉焦点/光标位置，
            # addWidget 本来就会把它安置到同一个父级上。
            while layout.count():
                layout.takeAt(0)
        for layout, indices in zip(self._rows, rows):
            # 两行时每行都靠左；只有一行时在 `gap` 处顶开，让右组贴右端
            split_at = self._gap if len(rows) == 1 else None
            for position, index in enumerate(indices):
                if split_at is not None and position == split_at:
                    layout.addStretch(1)
                layout.addWidget(self._widgets[index])
            if split_at is None:
                layout.addStretch(1)
        # 第二行空着时不留行间距（否则宽的时候会多出一条缝）
        self._outer.setSpacing(self._spacing if len(rows) > 1 else 0)
        for layout, indices in zip(self._rows, rows):
            for index in indices:
                self._widgets[index].setVisible(True)
        self.updateGeometry()
