"""起子进程时不许弹控制台窗口（`tu_shell_agent/procflags.py`）。

用户实测（Windows）："与模型会话，为什么在 windows 上会闪终端，而且应用打开的时候也会闪终端"。
根因是打包产物是**窗口程序**（spec 里 `console=False`），而 `CreateProcess` 在父进程没有控制台时
会**给子进程新建一个控制台窗口** —— 于是每起一个子进程就闪一下：开窗时的环境自检（三个
`--version`）、每轮对话的后端 CLI、以及长驻的 `opencode serve`（那个更是一直挂着黑框）。

这里守两件事：
1. 助手函数的形状（flags 组合、非 Windows 返回空、可注入平台）；
2. **源码级契约**：`tu_shell_agent/**` 里每一处 `subprocess.run/Popen` 都必须带上这个助手 ——
   漏一处的表现是"偶发闪一下终端"，靠人工 review 是查不出来的。
"""

from __future__ import annotations

import ast
import subprocess
from pathlib import Path

import pytest

from tu_shell_agent.procflags import (
    CREATE_NEW_PROCESS_GROUP,
    CREATE_NO_WINDOW,
    creation_flags,
    no_window_kwargs,
)

REPO = Path(__file__).resolve().parent.parent


# ── 助手本身的形状 ────────────────────────────────────────────────────


def test_posix_gets_no_extra_flags():
    """非 Windows 平台：不能传 creationflags（那是 Windows 专有的，非零值会直接报错）。"""
    assert creation_flags(platform="posix") == 0
    assert no_window_kwargs(platform="posix") == {}


def test_windows_hides_the_console_window():
    """Windows：CREATE_NO_WINDOW 必须带上；再给一份 SW_HIDE 的 startupinfo 兜底。"""
    kwargs = no_window_kwargs(platform="nt")
    assert kwargs["creationflags"] & CREATE_NO_WINDOW, "少了 CREATE_NO_WINDOW，就会闪终端"
    info = kwargs.get("startupinfo")
    if info is not None:                      # Linux 上没有 STARTUPINFO 这个类
        assert info.wShowWindow == getattr(subprocess, "SW_HIDE", 0)


def test_new_process_group_and_no_window_can_coexist():
    """要按整棵树杀的进程两个标志都要（按位或，不是二选一）。"""
    flags = creation_flags(platform="nt", new_process_group=True)
    assert flags & CREATE_NO_WINDOW
    assert flags & CREATE_NEW_PROCESS_GROUP


# ── 源码级契约：每一处子进程都要带上它 ─────────────────────────────────

HELPER = "no_window_kwargs"


def _launch_calls() -> list[tuple[str, int, bool]]:
    """扫出所有 `subprocess.run/Popen(...)`，返回 (文件, 行号, 是否带了助手)。"""
    found: list[tuple[str, int, bool]] = []
    for path in sorted((REPO / "tu_shell_agent").rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            if not (
                isinstance(func, ast.Attribute)
                and func.attr in {"run", "Popen", "check_output", "call"}
                and isinstance(func.value, ast.Name)
                and func.value.id == "subprocess"
            ):
                continue
            # `**no_window_kwargs(...)` 是一个 keyword(arg=None)；也接受字典 update 的写法：
            # 那种写法在同一个函数里出现 `popen_kwargs.update(no_window_kwargs(...))`。
            has_helper = any(
                keyword.arg is None and HELPER in ast.unparse(keyword.value)
                for keyword in node.keywords
            )
            if not has_helper:
                source = ast.unparse(tree)
                has_helper = f"{HELPER}(" in source and _updates_kwargs_before(tree, node)
            found.append((str(path.relative_to(REPO)), node.lineno, has_helper))
    return found


def _splatted_names(launch: ast.Call) -> set[str]:
    """这次调用里 `**` 展开进来的字典名（`Popen(argv, **popen_kwargs)` → {"popen_kwargs"}）。"""
    names: set[str] = set()
    for keyword in launch.keywords:
        if keyword.arg is None and isinstance(keyword.value, ast.Name):
            names.add(keyword.value.id)
    return names


def _updates_kwargs_before(tree: ast.AST, launch: ast.Call) -> bool:
    """同一文件里、在调用之前有没有用 `<那个字典>.update(no_window_kwargs(...))`。

    给"先攒一个 kwargs 字典、再 `Popen(argv, **kwargs)`"的写法准备（执行脚本 / serve /
    CLI 三条路都这么写：把 flags 塞进字典里比在调用处摊开更清楚）。
    """
    wanted = _splatted_names(launch)
    if not wanted:
        return False
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or node.lineno > launch.lineno:
            continue
        func = node.func
        if not (isinstance(func, ast.Attribute) and func.attr == "update"):
            continue
        if not (isinstance(func.value, ast.Name) and func.value.id in wanted):
            continue
        if any(HELPER in ast.unparse(argument) for argument in node.args):
            return True
    return False


def test_every_subprocess_launch_hides_the_console_window():
    """`tu_shell_agent/**` 里每一处 `subprocess.run/Popen` 都必须带 `no_window_kwargs()`。

    漏掉一处的表现是 Windows 上"偶发闪一下终端"，肉眼很难定位到底是哪条路径在闪 ——
    所以这里用静态检查把整个包扫一遍。**新加子进程调用时，这条用例会拦住你。**
    """
    offenders = [f"{path}:{line}" for path, line, ok in _launch_calls() if not ok]
    assert not offenders, (
        "这些子进程调用没有带上 no_window_kwargs()，在 Windows 上会闪终端："
        + "；".join(offenders)
    )


def test_the_contract_actually_scans_something():
    """别让上面那条用例因为"一个调用都没扫到"而空过（改了写法就会这样）。"""
    calls = _launch_calls()
    assert len(calls) >= 5, f"只扫到 {len(calls)} 处子进程调用，检查逻辑可能失效了：{calls}"


@pytest.mark.parametrize(
    "module",
    [
        "tu_shell_agent.shell_toolchain.execute",
        "tu_shell_agent.shell_toolchain.shellcheck",
        "tu_shell_agent.shell_toolchain.detect",
        "tu_shell_agent.opencode_adapter.server",
        "tu_shell_agent.opencode_adapter.models",
        "tu_shell_agent.agent_backends.cli_agent",
    ],
)
def test_known_launch_modules_import_the_helper(module: str):
    """用户实测会闪终端的那几条路径，逐个点名确认它们真的用上了助手。"""
    source = (REPO / (module.replace(".", "/") + ".py")).read_text(encoding="utf-8")
    assert HELPER in source, (
        f"{module} 没有用 {HELPER}（它是用户看到闪终端的那条路径之一）"
    )
