"""给只读文本控件加一条「就选中的内容提问」的右键菜单项。

用户要求：**对话里可以选中代码来提问**（"选中代码进行对话，询问代码"）。实现方式不是在
界面上再摆一个"引用"按钮，而是复用用户已经在做的动作——选中、右键：菜单里多一项，
点了就把这段选中内容作为引用带进对话框。

为什么不自己写整套右键菜单：复制 / 全选 / 清空这些项的行为随平台与输入法变，自己实现一份
迟早会缺东西。这里用 `createStandardContextMenu()` 拿标准菜单，只往最前面插一项。

新行符要转换：`QTextCursor.selectedText()` 用的是 U+2029（段落分隔符）而不是 `\n`，
直接塞进提示词里会变成一行（实测：交给模型的是一整行长文本）。
"""

from __future__ import annotations

from typing import Callable

from PySide6.QtCore import QPoint, Qt
from PySide6.QtGui import QAction
from PySide6.QtWidgets import QMenu, QWidget

# U+2029 段落分隔；U+2028 行分隔（某些输入法/粘贴路径会产生）
_PARAGRAPH_SEPARATORS = ("\u2028", "\u2029")


def selected_text(widget) -> str:
    """控件里选中的文本，新行统一成 `\\n`；没有选中返回空串。

    两种控件都要支持：文本编辑类控件（`QPlainTextEdit` / `QTextBrowser`…）走 `textCursor()`，
    而对话记录里的正文是 `QLabel`（卡片式排版，见 `ui/chat_view.py`）—— 它只有 `selectedText()`，
    没有游标对象。少写这一条分支，记录区里的"选中→提问"就会静默失效（菜单里永远缺那一项）。
    """
    cursor_getter = getattr(widget, "textCursor", None)
    if callable(cursor_getter):
        cursor = cursor_getter()
        if cursor is None or not cursor.hasSelection():
            return ""
        text = cursor.selectedText()
    else:
        text = str(widget.selectedText() or "") if hasattr(widget, "selectedText") else ""
    for separator in _PARAGRAPH_SEPARATORS:
        text = text.replace(separator, "\n")
    return text


def build_menu(widget: QWidget, on_ask: Callable[[str], None], label: str) -> QMenu:
    """右键菜单 + 置顶的「提问」项；没有选中内容时不加这一项。

    能给出标准菜单的控件（文本编辑类）就用它的标准菜单（复制 / 全选 / 清空这些项的行为
    随平台与输入法变，自己实现一份迟早会缺东西）；`QLabel` 没有标准菜单，就给它一份最小的
    「提问 / 复制 / 全选」—— 三个动作在这里都有确定语义，不会缺东西。

    与弹出分开，是为了能直接断言菜单内容（`exec()` 是模态循环，测试里不能调）。
    """
    maker = getattr(widget, "createStandardContextMenu", None)
    menu: QMenu = maker() if callable(maker) else _label_menu(widget)
    text = selected_text(widget)
    if not text.strip():
        return menu
    action = QAction(label, menu)
    action.triggered.connect(lambda _checked=False, value=text: on_ask(value))
    existing = menu.actions()
    if existing:
        menu.insertAction(existing[0], action)
        menu.insertSeparator(existing[0])
    else:
        menu.addAction(action)
    return menu


def _label_menu(widget: QWidget) -> QMenu:
    """给 `QLabel` 这类没有标准菜单的控件造一份最小菜单。"""
    menu = QMenu(widget)
    copy_action = QAction("复制", menu)
    copy_action.triggered.connect(lambda _checked=False: _copy(widget))
    select_action = QAction("全选", menu)
    select_action.triggered.connect(lambda _checked=False: widget.setSelection(0, len(widget.text())))
    menu.addAction(copy_action)
    menu.addAction(select_action)
    return menu


def _copy(widget: QWidget) -> None:
    from PySide6.QtWidgets import QApplication

    text = selected_text(widget) or str(getattr(widget, "text", lambda: "")() or "")
    clipboard = QApplication.clipboard()
    if clipboard is not None:
        clipboard.setText(text)


def install_ask_action(
    widget: QWidget,
    on_ask: Callable[[str], None],
    *,
    label: str = "就选中的代码提问",
) -> None:
    """把「选中 → 提问」接进控件的右键菜单。"""
    widget.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
    widget.customContextMenuRequested.connect(
        lambda point: _popup(widget, point, on_ask, label)
    )


def exec_menu(menu: QMenu, global_pos: QPoint) -> None:
    """弹出菜单（模态）。

    单独留一个函数是为了能被替换：PySide6 的 C++ 方法（`QMenu.exec`）**无法**用
    monkeypatch 覆盖（实测赋值被静默忽略），测试要拦掉这个模态循环只能拦这一层。
    """
    menu.exec(global_pos)


def _popup(widget: QWidget, point: QPoint, on_ask: Callable[[str], None], label: str) -> None:
    menu = build_menu(widget, on_ask, label)
    try:
        exec_menu(menu, widget.mapToGlobal(point))
    finally:
        menu.deleteLater()
