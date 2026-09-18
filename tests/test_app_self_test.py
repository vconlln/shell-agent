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


# ── 一键打包脚本的契约（两个平台各一个，别让它们悄悄腐坏） ──────────────


def test_platform_build_scripts_point_at_the_shared_spec_and_their_own_dist():
    """两个一键脚本必须共用同一份 spec，并且各写各的产物目录。

    这类"文档/脚本里的路径"最容易在重构后腐烂：脚本还在，路径已经不对，
    而失败发生在别人机器上（Windows 那个尤其没法在开发机验证）。
    """
    from pathlib import Path

    repo = Path(__file__).resolve().parent.parent
    sh = (repo / "packaging" / "linux" / "build.sh").read_text(encoding="utf-8")
    bat = (repo / "packaging" / "windows" / "build.bat").read_text(encoding="utf-8")

    # 共用同一份 spec 与入口（平台无关）
    assert "tu-shell-agent.spec" in sh and "tu-shell-agent.spec" in bat
    assert (repo / "packaging" / "tu-shell-agent.spec").is_file()
    assert (repo / "packaging" / "entry.py").is_file()

    # 各写各的产物目录：只认 --distpath 那个参数，不看注释
    # （两个脚本的注释里都会提到对方的目录，按全文搜会误判）
    import re

    assert re.search(r"--distpath\s+\S*dist/linux", sh), "--distpath 没指向 dist/linux"
    assert re.search(r"--distpath\s+\"?dist\\windows", bat), "--distpath 没指向 dist\\windows"

    # 两个脚本都要跑产物自检（打包完必须验证产物真能起来）
    assert "--self-test" in sh and "--self-test" in bat

    # 批处理必须是 CRLF：老版本 cmd 对 LF-only 的 .bat 处理有坑
    assert b"\r\n" in (repo / "packaging" / "windows" / "build.bat").read_bytes()

    # 批处理的命令必须是纯 ASCII（中文只出现在 echo/rem 里，否则代码页切换会咬人）
    for line in bat.splitlines():
        stripped = line.strip()
        if stripped and not stripped.lower().startswith(("rem", "echo", "::")):
            assert stripped.isascii(), f"命令里出现非 ASCII 字符：{line!r}"


def test_linux_build_script_is_executable():
    """Linux 脚本要能直接执行（git 里必须带着可执行位）。"""
    import os
    from pathlib import Path

    script = Path(__file__).resolve().parent.parent / "packaging" / "linux" / "build.sh"
    assert os.access(script, os.X_OK), "packaging/linux/build.sh 没有可执行位"
