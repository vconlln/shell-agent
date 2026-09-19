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
        return 0

    # 开窗之后立刻自检三件套（规格 §9：缺一不可）：探测起子进程，放在控制器线程里跑，
    # 免得窗口先冻住几秒。放在这里而不是 MainWindow 里，是为了让构造窗口本身不产生副作用。
    if window.controller is not None:
        window.controller.recheck_environment()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
