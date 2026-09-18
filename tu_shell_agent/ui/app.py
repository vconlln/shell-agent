"""界面入口：装配 QApplication 与主窗口。"""

from __future__ import annotations

import sys

from PySide6.QtWidgets import QApplication

from .main_window import MainWindow


def main(argv: list[str] | None = None) -> int:
    app = QApplication(argv if argv is not None else sys.argv)
    app.setApplicationName("tu-shell-agent")
    window = MainWindow()
    window.show()
    # 开窗之后立刻自检三件套（规格 §9：缺一不可）：探测起子进程，放在控制器线程里跑，
    # 免得窗口先冻住几秒。放在这里而不是 MainWindow 里，是为了让构造窗口本身不产生副作用。
    if window.controller is not None:
        window.controller.recheck_environment()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
