@echo off
rem 一键在 Windows 上打出 exe：建虚拟环境 → 装依赖 → 打包 → 产物自检。
rem
rem ⚠ 这个脚本**没有在 Windows 上执行过**（开发机是 Linux，PyInstaller 不能交叉编译）。
rem    它只是把 packaging/build.md 第 1-3 节的命令按顺序抄了一遍；出问题请以 build.md 为准。
rem    用法：把整个仓库拷到 Windows，双击本文件（或在 cmd 里运行）。
setlocal
chcp 65001 >nul
cd /d "%~dp0.."
echo 仓库根目录：%CD%
echo.

if not exist ".venv\Scripts\python.exe" (
  echo [1/4] 建虚拟环境 .venv ...
  py -3 -m venv .venv || goto :fail
) else (
  echo [1/4] 已存在 .venv，跳过
)

echo [2/4] 安装依赖（PySide6 + pyinstaller + 测试依赖）...
".venv\Scripts\python.exe" -m pip install --upgrade pip
".venv\Scripts\python.exe" -m pip install -e ".[ui,dev]" || goto :fail
".venv\Scripts\python.exe" -m pip install pyinstaller || goto :fail

echo [3/4] 打包（one-folder）...
".venv\Scripts\python.exe" -m PyInstaller --clean --noconfirm --distpath dist --workpath build packaging\tu-shell-agent.spec || goto :fail

echo [4/4] 产物自检（应打印 exit=0）...
rem 注意：spec 里 console=False，产物是 GUI 子系统 exe，直接调用不会等待、也读不到退出码，
rem 所以必须用 start /wait（理由见 packaging/build.md 第 3 节）。
rem 自检会短暂闪出一个窗口，然后自己退出 —— 这是正常的。
start /wait "" "dist\tu-shell-agent\tu-shell-agent.exe" --self-test
echo self-test exit=%ERRORLEVEL%

echo.
echo ============================================================
echo  产物：%CD%\dist\tu-shell-agent\tu-shell-agent.exe
echo  双击它即可打开界面。
echo  生成脚本需要 opencode 已登录：先跑一次  opencode auth login
echo  手测清单（规格 §14）见 packaging\build.md 第 6 节。
echo ============================================================
pause
exit /b 0

:fail
echo.
echo *** 失败：看上面的输出。对照 packaging\build.md 的前置与第 8 节排错。 ***
pause
exit /b 1
