"""界面入口：装配 QApplication 与主窗口。"""

from __future__ import annotations

import sys

from PySide6.QtWidgets import QApplication

from .main_window import MainWindow
from .settings import APP_NAME
from .theme import apply_theme

# 自己建过的 QApplication 在这里持一份强引用：Qt 单例被垃圾回收之后再建第二个，
# 进程会崩在退出路径上（测试里连着调两次 main() 就会走到这一步）。
_APP: QApplication | None = None


def _self_check_appearance(window) -> list[str]:
    """在打包冒烟里顺带验一遍**外观链路**（两个平台跑的是同一套检查）。

    为什么必须验：自绘亚克力要读壁纸图片，而图片解码依赖 Qt 的 `imageformats` 插件。
    PyInstaller **漏收插件时不会报错** —— 界面上表现为"亚克力悄悄退化成半透明"，
    只有在这台机器上手动切一遍背景效果才会发现。这一步就是把它变成自检能拦住的事。

    返回问题列表（空 = 全部通过）。
    """
    import tempfile
    from pathlib import Path as _Path

    from PySide6.QtCore import QPoint
    from PySide6.QtGui import QColor, QImage, QPainter

    from . import acrylic as acrylic_module
    from . import theme as theme_module

    problems: list[str] = []

    # 1) 四种背景效果的样式表都能生成（纯字符串，但能挡住令牌缺键这类崩在启动路径上的错误）
    for mode in ("off", "translucent", "blur", "acrylic"):
        try:
            theme_module.build_stylesheet(backdrop=mode)
            theme_module.build_palette(backdrop=mode)
        except Exception as error:  # noqa: BLE001 - 自检要把任何异常变成"不通过"
            problems.append(f"{mode} 模式的样式表/调色板生成失败：{error}")

    # 2) 自绘模糊真的能跑：临时造一张图 → 加载 → 模糊 → 铺层
    with tempfile.TemporaryDirectory() as tmp:
        sample = _Path(tmp, "wall.png")
        image = QImage(96, 64, QImage.Format.Format_ARGB32_Premultiplied)
        image.fill(QColor(120, 130, 140))
        if not image.save(str(sample)):
            problems.append("无法写出临时图片（PNG 编码不可用）")
        else:
            if acrylic_module.backdrop_image(str(sample), 96, 64, 20) is None:
                problems.append("自绘模糊不可用：图片加载或模糊失败（多半缺 imageformats 插件）")

    # 3) 窗口真的画得出来（各模式下都不是空窗）
    for mode in ("off", "acrylic"):
        window.settings.backdrop = mode
        window.apply_appearance()
        rendered = QImage(window.size(), QImage.Format.Format_ARGB32_Premultiplied)
        rendered.fill(QColor(0, 0, 0, 0))
        painter = QPainter(rendered)
        window.render(painter, QPoint(0, 0))       # PySide6 要求显式给 targetOffset
        painter.end()
        opaque = sum(
            1
            for x in range(0, rendered.width(), 17)
            for y in range(0, rendered.height(), 17)
            if rendered.pixelColor(x, y).alpha() > 0
        )
        if opaque == 0:
            problems.append(f"{mode} 模式下窗口渲染为空")
    window.settings.backdrop = "off"
    window.apply_appearance()
    return problems


def main(argv: list[str] | None = None) -> int:
    args = list(argv if argv is not None else sys.argv[1:])
    # `--self-test` 是给打包冒烟用的：构造并绘制一遍主窗口就返回，不进事件循环。
    # PyInstaller 产物没法在 CI 里点按钮，它只能靠这个开关证明 Qt 插件真的被收集齐了。
    self_test = "--self-test" in args

    global _APP
    # QApplication 是单例：进程里已经有一个时再造第二个会直接 RuntimeError，
    # 所以先复用（pytest-qt 会替整个测试会话建一个）。
    app = QApplication.instance()
    if app is None:
        # Qt 会挑走自己认识的参数，剩下的留在 argv 里没有副作用；但 `--self-test`
        # 交给 Qt 只会被当成未知参数，干脆先摘掉。
        app = QApplication([sys.argv[0], *[a for a in args if a != "--self-test"]])
    _APP = app
    app.setApplicationName(APP_NAME)
    # 主题在**建窗口之前**装：控件构造时就会读调色板，晚了会出现"先按原生样式画一遍
    # 再被刷掉"的闪动，而且自绘控件（行号槽）会拿着旧调色板。
    apply_theme(app)

    window = MainWindow()
    # 外观必须在 **show() 之前**应用：`WA_TranslucentBackground` 这类窗口属性只有在窗口
    # 创建/显示之前设上才稳（先 show 再设，属性设了但窗口已经是"不透明"的表面 ——
    # 用户反馈的"半透明没有效果"就有这一半原因）。
    window.apply_appearance()
    window.show()
    if self_test:
        # 走一遍真实构造与绘制路径（无头后端下无副作用）后立刻返回。
        # 必须在 recheck_environment 之前返回：它会起子进程探测三件套，在打包冒烟里既慢，
        # 又会让"这台机器缺工具"看起来像"打包坏了"，无头/CI 下还可能把冒烟进程拖住。
        app.processEvents()
        problems = _self_check_appearance(window)
        if problems:
            print("外观自检失败：" + "；".join(problems), file=sys.stderr)
            return 1
        print("外观自检通过：四种背景效果 + 自绘模糊 + 面板渲染都可用")
        return 0

    # 开窗之后立刻自检三件套（规格 §9：缺一不可）：探测起子进程，放在控制器线程里跑，
    # 免得窗口先冻住几秒。放在这里而不是 MainWindow 里，是为了让构造窗口本身不产生副作用。
    if window.controller is not None:
        window.controller.recheck_environment()
    exit_code = app.exec()
    # 退出前的最后一道闸：还有托管线程没结束就立刻退出进程。
    # Qt 在析构"还在跑的 QThread"时会 abort（coredump 确认过 SIGABRT），
    # 而窗口的 closeEvent 不一定走得到（比如被直接销毁）。
    from .workers import wait_or_exit

    wait_or_exit()
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
