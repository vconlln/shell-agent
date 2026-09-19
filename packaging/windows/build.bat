@echo off
rem ============================================================================
rem  一键打包（Windows）：建 venv - 装依赖 - PyInstaller - 产物自检
rem
rem  产物：dist\windows\tu-shell-agent\tu-shell-agent.exe
rem  用法：双击本文件，或在 cmd 里运行 packaging\windows\build.bat
rem
rem  ！！本脚本没有在 Windows 上执行过！！ 开发机是 Linux，而 PyInstaller 不支持
rem  交叉编译，所以 Windows 的 exe 只能在 Windows 上打，这个脚本也就无法在开发机上验证。
rem  它只是把 packaging\build.md 第 1-3 节的命令按顺序抄了一遍；若某一步失败，
rem  请打开 build.md 逐条手动跑，那里的每一步都注明了原因。
rem
rem  与它对应的是 Linux 的 packaging/linux/build.sh（那个已在 Linux 上实测通过）。
rem  两者共用同一份 spec 与入口：packaging\tu-shell-agent.spec + packaging\entry.py
rem ============================================================================

rem 65001 = UTF-8：本文件里的中文提示按 UTF-8 存盘，先把控制台代码页切过去，
rem 否则中文提示会显示成乱码。注意命令本身全是 ASCII，中文只出现在 echo/rem 里，
rem 且提示文字里不含括号、&、管道、百分号 —— 那些字符在批处理里有语法含义。
chcp 65001 >nul
setlocal
pushd "%~dp0..\.." || goto :fail
echo 仓库根目录：%CD%
echo.

rem ---- 步骤 1/4：虚拟环境 ----------------------------------------------------
if exist ".venv\Scripts\python.exe" goto :have_venv
echo [1/4] 创建虚拟环境 .venv ...
py -3 -m venv .venv
if exist ".venv\Scripts\python.exe" goto :deps
echo py 启动器不可用，改用 python 再试一次 ...
python -m venv .venv || goto :fail
goto :deps

:have_venv
echo [1/4] 已存在 .venv，跳过创建

rem ---- 步骤 2/4：依赖 --------------------------------------------------------
:deps
echo [2/4] 准备依赖 ...
rem 三步的失败后果不一样：升级 pip 失败只警告；PySide6 与 pyinstaller 缺失就打不出产物；
rem 而"可编辑安装项目"只影响跑测试与控制台入口，失败不该把整次打包判死
rem （网络抖动时它会因为拉不到 setuptools 而失败，但产物其实能出）。
".venv\Scripts\python.exe" -m pip install --upgrade pip
echo   - PySide6 与 pyinstaller，打包必需
".venv\Scripts\python.exe" -m pip install "PySide6>=6.11" pyinstaller || goto :fail
echo   - 项目本身与测试依赖，可选，失败不影响出产物
rem pyproject 里用 [tool.setuptools.packages.find] 把自动发现限定成 tu_shell_agent*，
rem 否则平铺布局会因为"发现多个顶层包"直接拒绝构建。
".venv\Scripts\python.exe" -m pip install -e ".[ui,dev]"
rem 用 goto 而不是 `if errorlevel 1 echo <中文>`：让中文只出现在 echo 行上，
rem 脚本契约测试才能用一条简单规则守住"命令全 ASCII"（中文在 if 里同样是文本，
rem 但规则要区分"命令"和"被 echo 的文本"就得解析批处理语法，太脆）。
if not errorlevel 1 goto :deps_ok
echo   ！可编辑安装失败，多半是网络或代理拉不到构建依赖，打包继续
:deps_ok

rem ---- 步骤 3/4：打包 --------------------------------------------------------
echo [3/4] 打包：one-folder ...
rem --distpath / --workpath 相对当前目录解析，所以前面 pushd 到了仓库根。
rem 两个平台分目录：dist\windows 与 dist/linux 各自独立，互不覆盖。
".venv\Scripts\python.exe" -m PyInstaller --clean --noconfirm --distpath "dist\windows" --workpath "build\windows" "packaging\tu-shell-agent.spec" || goto :fail

rem ---- 步骤 4/4：产物自检 ----------------------------------------------------
set "APP=dist\windows\tu-shell-agent\tu-shell-agent.exe"
if not exist "%APP%" goto :missing
echo [4/4] 产物自检，应打印 exit=0 ...
rem spec 里 console=False，产物是 GUI 子系统 exe：直接调用不会等待、也读不到退出码，
rem 所以必须用 start /wait 等它跑完。自检会短暂闪出一个窗口后自己退出，属正常。
rem 这里不设 QT_QPA_PLATFORM：Windows 上直接开窗更接近真实使用，也少依赖一个插件。
start /wait "" "%APP%" --self-test
echo self-test exit=%ERRORLEVEL%
if not "%ERRORLEVEL%"=="0" goto :selftest_failed

rem ---- 插件抽查：PyInstaller 漏收 Qt 插件时不会报错，后果是"窗口起不来"或
rem      "自绘模糊读不了壁纸、悄悄退化成半透明"。与 Linux 脚本抽查同一组能力。
set "PLUGINS=dist\windows\tu-shell-agent\_internal\PySide6\Qt\plugins"
if not exist "%PLUGINS%\platforms\qwindows.dll" goto :missing_plugins
if not exist "%PLUGINS%\imageformats\qjpeg.dll" goto :missing_plugins
echo 插件抽查通过：platforms 与 imageformats/jpeg 都在
echo.
echo ============================================================
echo  产物：%CD%\%APP%
echo  exe 体积（字节）：
for %%F in ("%APP%") do echo   %%~zF
echo  双击它即可打开界面
echo  生成脚本需要 opencode 已登录：先跑一次  opencode auth login
echo  手测清单见 packaging\build.md 第 6 节
echo ============================================================
popd
pause
exit /b 0

:missing
echo *** 打包结束但没有找到 %APP% ***
goto :fail

:selftest_failed
echo *** 自检没有返回 0：产物能生成但起不来，对照 packaging\build.md 第 3 节排错 ***
goto :fail

:missing_plugins
echo *** 缺 Qt 插件：platforms\qwindows.dll 缺失会导致窗口起不来；
echo     imageformats\qjpeg.dll 缺失会让自绘模糊静默失效 ***
goto :fail

:fail
echo.
echo *** 失败：看上面的输出；对照 packaging\build.md 的前置与第 8 节排错 ***
popd
pause
exit /b 1
