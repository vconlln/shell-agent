"""打包冒烟的自检契约：`--self-test` 构造一遍主窗口就返回，不进事件循环。

PyInstaller 产物没法在 CI 里点按钮，`--self-test` 就是"Qt 插件确实被收集齐了"的唯一证据，
所以它必须满足两件事：立刻返回 0（不挂住），并且不顺手跑一遍环境探测。
"""

from tu_shell_agent.ui import run_controller
from tu_shell_agent.ui.app import main


def test_self_test_builds_window_and_exits_zero():
    assert main(["--self-test"]) == 0


def test_self_test_does_not_probe_environment(monkeypatch):
    """自检分支必须在 `recheck_environment()` 之前返回（规格 §9 的三件套探测会起子进程）。

    这不是洁癖：探测会跑 `opencode --version` / `bash --version` / `shellcheck --version`，
    在打包冒烟里既慢，又会让"这台机器缺工具"看起来像"打包坏了"，无头环境下还可能把冒烟进程拖住。
    """
    probed: list[object] = []
    monkeypatch.setattr(
        run_controller.RunController, "recheck_environment", lambda self: probed.append(self)
    )

    assert main(["--self-test"]) == 0
    assert probed == [], "自检分支不该触发环境探测"
