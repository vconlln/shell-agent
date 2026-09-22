"""按钮接线：`clicked` 会带一个 bool，槽函数接不住就会**静默失效**。

背景（用户实测）：点了底栏的「控制台」没反应。根因是

    self.console_button.clicked.connect(self.open_console)

`clicked` 总是带一个 `checked=False`，于是 `open_console(False)` → `if page is not None` 成立
→ `tool_tabs.indexOf(False)` 抛 TypeError → Qt 打印异常、槽函数中断、弹窗永远不出现。
之前没有一条用例走"真点击"，所以没人发现。

这里守着两件事：
1. 任何 `clicked.connect(self.方法)` 的方法都**不能有位置参数**（用 lambda 吞掉 checked）；
2. 关键按钮真的点一下，确认目标行为发生。
"""

from __future__ import annotations

import ast
import importlib
import inspect
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
UI_ROOT = REPO / "tu_shell_agent" / "ui"


def _clicked_targets() -> list[tuple[str, str, str]]:
    """扫出所有 `<X>.clicked.connect(self.<方法>)`：返回 (模块路径, 类名, 方法名)。"""
    found: list[tuple[str, str, str]] = []
    for path in sorted(UI_ROOT.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for cls in [node for node in ast.walk(tree) if isinstance(node, ast.ClassDef)]:
            own = {
                method.name
                for method in cls.body
                if isinstance(method, (ast.FunctionDef, ast.AsyncFunctionDef))
            }
            for node in ast.walk(cls):
                if not isinstance(node, ast.Call) or not node.args:
                    continue
                func = node.func
                if not (isinstance(func, ast.Attribute) and func.attr == "connect"):
                    continue
                if not ast.unparse(func.value).endswith(".clicked"):
                    continue
                arg = node.args[0]
                if (
                    isinstance(arg, ast.Attribute)
                    and isinstance(arg.value, ast.Name)
                    and arg.value.id == "self"
                    and arg.attr in own
                ):
                    found.append((str(path.relative_to(REPO)), cls.name, arg.attr))
    return found


def test_clicked_slots_do_not_take_positional_arguments():
    """`clicked` 的 checked 参数不许流进槽函数的位置参数（否则静默失效）。

    这条是"点了控制台没反应"的通用化：任何一个槽只要**有位置参数**，Qt 就会把 `False`
    塞进去；带默认值的参数（`page=None`）照样中招 —— 因为 `False is not None`。
    要接参数就用 `lambda: ...` 或 `lambda _checked=False: ...` 显式吞掉。
    """
    offenders: list[str] = []
    for module_path, cls_name, method_name in _clicked_targets():
        module_name = module_path[: -len(".py")].replace("/", ".")
        module = importlib.import_module(module_name)
        cls = getattr(module, cls_name, None)
        if cls is None:
            offenders.append(f"{module_path}:{cls_name} 导入不到")
            continue
        signature = inspect.signature(getattr(cls, method_name))
        positional = [
            parameter
            for parameter in list(signature.parameters.values())[1:]
            if parameter.kind
            in (parameter.POSITIONAL_ONLY, parameter.POSITIONAL_OR_KEYWORD)
        ]
        if positional:
            offenders.append(
                f"{module_path}:{cls_name}.{method_name} 有位置参数 "
                f"{[p.name for p in positional]} —— clicked 会把 checked 塞进去"
            )
    assert not offenders, "这些 clicked 接线会被 Qt 的 checked 参数带崩：" + "；".join(offenders)


def test_console_button_click_opens_the_dialog(qtbot):
    """**真点一下**底栏的「控制台」：弹窗要出现，且停在运行页。"""
    from tu_shell_agent.ui.main_window import MainWindow
    from tu_shell_agent.ui.settings import AppSettings

    window = MainWindow(wire_controller=False, settings=AppSettings())
    qtbot.addWidget(window)
    window.resize(1440, 900)
    window.show()
    qtbot.waitExposed(window)
    assert not window.console_dialog.isVisible()

    window.console_button.click()
    qtbot.wait(30)

    assert window.console_dialog.isVisible(), "点了「控制台」弹窗没出来"
    assert window.start_button.isVisible(), "运行页的操作按钮应当可见"

    window.console_close_button.click()
    qtbot.wait(30)
    assert not window.console_dialog.isVisible(), "「关闭」没关掉弹窗"


def test_open_console_tolerates_the_checked_flag(qtbot):
    """退一步：即使有人再把 `clicked` 直接连到 `open_console`，也不许崩。

    `open_console(page=False)` 这种调用在语义上是"没指定页"，必须照常开窗
    （弹窗打不开是最难查的症状：界面毫无反应、日志里只有一行异常）。
    """
    from tu_shell_agent.ui.main_window import MainWindow
    from tu_shell_agent.ui.settings import AppSettings

    window = MainWindow(wire_controller=False, settings=AppSettings())
    qtbot.addWidget(window)
    window.resize(1440, 900)
    window.show()
    qtbot.waitExposed(window)

    window.open_console(False)          # type: ignore[arg-type] - 故意传 Qt 的 checked
    qtbot.wait(30)
    assert window.console_dialog.isVisible()
