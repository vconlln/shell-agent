# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller 规格：one-folder 产物，Linux 与 Windows 共用（PyInstaller 不交叉编译，各打各的）。

通常不直接调它，而是用两个平台各自的一键脚本（它们会 cd 到仓库根再调本文件）：

    Linux   : bash packaging/linux/build.sh    → dist/linux/tu-shell-agent/
    Windows : packaging\windows\build.bat      → dist\windows\tu-shell-agent\

手写命令时（**必须在仓库根目录**执行，--distpath/--workpath 相对当前目录解析）：

    .venv/bin/python -m PyInstaller --clean --noconfirm \
        --distpath dist/linux --workpath build/linux packaging/tu-shell-agent.spec

入口是 `packaging/entry.py`，不是计划里写的 `tu_shell_agent/ui/app.py`：PyInstaller 把入口
脚本当 `__main__` 跑，而 `__main__` 没有 `__package__`，`app.py` 顶部的相对导入会死在
`ImportError: attempted relative import with no known parent package`（实测见 docs 里的
packaging/build.md）。薄壳入口做绝对导入，绕开这个坑。

路径一律用 SPECPATH 拼绝对路径：SPECPATH 是 PyInstaller 注入的「spec 文件所在目录」，
这样无论从哪个目录调用，pathex 都只会指向本仓库，不会把仓库外的目录混进模块搜索路径。
"""
from os.path import dirname, join

from PyInstaller.utils.hooks import collect_submodules

REPO_ROOT = dirname(SPECPATH)

block_cipher = None

a = Analysis(
    [join(SPECPATH, "entry.py")],
    # 入口脚本自己就能 import 到 tu_shell_agent（绝对导入），pathex 只是兜底：
    # 让分析期的模块搜索路径包含仓库根目录。
    pathex=[REPO_ROOT],
    binaries=[],
    datas=[],
    # Qt 插件的收集由 PyInstaller 自带的 hook-PySide6.* 负责（platforms/、styles/ 等），
    # 这里只补 QtWidgets 的子模块，防 hooked 分析漏掉运行时才用到的模块。
    hiddenimports=collect_submodules("PySide6.QtWidgets") + ["tu_shell_agent.ui.app"],
    hookspath=[],
    runtime_hooks=[],
    excludes=["tkinter", "PySide6.QtWebEngineCore"],   # 不用的重件排除，产物体积可控
    cipher=block_cipher,
)
pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name="tu-shell-agent", console=False)
coll = COLLECT(exe, a.binaries, a.zipfiles, a.datas, name="tu-shell-agent")
