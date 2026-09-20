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

    # 批处理必须**整份**都是 ASCII。
    # 用户实测踩到过：UTF-8 的 .bat 里放中文，cmd 按当前代码页逐行读，多字节字符会被
    # 从中间切断，注释碎片被当成命令执行（报 '...' is not recognized）。中文说明写在
    # packaging/build.md 里，脚本本身只用 ASCII。
    try:
        batch_bytes = (repo / "packaging" / "windows" / "build.bat").read_bytes()
        batch_bytes.decode("ascii")
    except UnicodeDecodeError as error:
        raise AssertionError(f"packaging/windows/build.bat 里出现了非 ASCII 字节：{error}") from error


def test_linux_build_script_is_executable():
    """Linux 脚本要能直接执行（git 里必须带着可执行位）。"""
    import os
    from pathlib import Path

    script = Path(__file__).resolve().parent.parent / "packaging" / "linux" / "build.sh"
    assert os.access(script, os.X_OK), "packaging/linux/build.sh 没有可执行位"


def test_self_test_fails_when_the_image_pipeline_is_broken(monkeypatch):
    """自检要能拦住"自绘模糊静默失效"。

    打包最容易出的静默故障就是漏收 Qt 的 `imageformats` 插件：PyInstaller 不报错，
    界面上只是"亚克力悄悄退化成半透明"，只有手动切一遍背景效果才发现。
    所以自检里必须有一步真的走一遍"图片 → 模糊 → 铺层"，这里把那一环打断，自检必须转成失败。
    """
    from tu_shell_agent.ui import acrylic as acrylic_module

    monkeypatch.setattr(acrylic_module, "backdrop_image", lambda *a, **k: None)
    assert main(["--self-test"]) == 1


def test_self_test_fails_when_a_backdrop_mode_cannot_be_built(monkeypatch):
    """令牌表缺键这类"只在某个模式下才崩"的错误，也要在自检里现形。"""
    from tu_shell_agent.ui import theme as theme_module

    real = theme_module.build_stylesheet

    def broken(*, scale=1.0, ui_font="", mono_font="", backdrop="off"):
        if backdrop == "blur":
            raise KeyError("bg_menu")
        return real(scale=scale, ui_font=ui_font, mono_font=mono_font, backdrop=backdrop)

    monkeypatch.setattr(theme_module, "build_stylesheet", broken)
    assert main(["--self-test"]) == 1


def test_build_scripts_check_the_same_qt_plugins():
    """两个平台的打包脚本要抽查**同一组能力**的 Qt 插件，而且都用递归搜索。

    抽查的意义：漏收插件时 PyInstaller 不报错 —— 少平台插件窗口起不来，少 imageformats
    则自绘模糊静默失效（退化成半透明）。两边抽查同一组，才不会出现"Linux 产物好着、
    Windows 产物少了 jpeg 解码"这种只有用户才会遇到的不一致。

    用**递归搜索**而不是写死路径，是因为用户实测在 Windows 上写死的路径根本不存在
    （PySide6 各版本/wheel 把插件放在哪并不固定）：检查本身不能因为布局不同就误判失败。
    """
    from pathlib import Path

    repo = Path(__file__).resolve().parent.parent
    sh = (repo / "packaging" / "linux" / "build.sh").read_text(encoding="utf-8")
    bat = (repo / "packaging" / "windows" / "build.bat").read_text(encoding="utf-8")

    # Linux：递归找 libqxcb.so / libqwayland.so / libqjpeg.so
    assert "find " in sh and "-print -quit" in sh, "Linux 脚本没做递归搜索"
    for name in ("libqxcb.so", "libqwayland.so", "libqjpeg.so"):
        assert name in sh, f"Linux 脚本没抽查 {name}"

    # Windows：递归找 qwindows.dll / qjpeg.dll
    assert "dir /s /b" in bat, "Windows 脚本没做递归搜索"
    assert "qwindows.dll" in bat and "qjpeg.dll" in bat, "Windows 脚本没抽查平台/图片插件"

    # 两边都要有这一步，并且失败就判整体失败
    assert "插件抽查" in sh
    assert "missing_platform" in bat and "missing_jpeg" in bat


def test_ui_layer_keeps_its_platform_branches_in_one_place():
    """界面层的平台分支只允许出现在两个文件里（两版一致性靠这条守住）。

    外观、布局、主题、会话、模型这些代码一旦长出 `sys.platform` 分支，Windows 与 Linux
    就会开始漂移 —— 而 Windows 侧在开发机上根本跑不到。允许的两个文件：
      - `ui/backdrop.py`：窗口半透明与系统模糊（DWM / KWin）
      - `ui/acrylic.py`：Windows 壁纸探测（注册表 + 主题缓存）
    """
    from pathlib import Path

    repo = Path(__file__).resolve().parent.parent
    allowed = {"backdrop.py", "acrylic.py"}
    offenders: list[str] = []
    for path in sorted((repo / "tu_shell_agent" / "ui").rglob("*.py")):
        text = path.read_text(encoding="utf-8")
        for marker in ("sys.platform", "os.name ==", "platform.system("):
            if marker in text and path.name not in allowed:
                offenders.append(f"{path.relative_to(repo)} 用了 {marker}")
    assert not offenders, "界面层出现了平台分支：" + "；".join(offenders)
