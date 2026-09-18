"""PyInstaller 的入口脚本：一层绝对导入的薄壳，只做这件事。

为什么不直接把 `tu_shell_agent/ui/app.py` 当入口：PyInstaller 把入口脚本当 `__main__`
执行，而 `__main__` 没有 `__package__`，`app.py` 里的相对导入（`from .main_window import
MainWindow`）会直接抛 `ImportError: attempted relative import with no known parent package`
（本机实测：`--self-test` 退出码 1、stderr 就是这个 traceback）。从 `packaging/` 这个
不在包内的目录做绝对导入，`tu_shell_agent` 才会被当成真正的包加载，相对导入才成立。
"""

from tu_shell_agent.ui.app import main

raise SystemExit(main())
