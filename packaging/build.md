# 打包与 Windows 验收（Plan 2 / 任务 10）

本文件三件事：**怎么打包**（Linux 本机已验证 / Windows 待手测）、**打包后怎么自检**、
**规格 §14 的 Windows 手测清单**（逐条摘抄成勾选表）。

结论先写在最前面：Linux one-folder 产物 `--self-test` 实测 **exit=0**（0.35s）；
Windows 的 exe 必须在 Windows 上打，本文件里所有标了「⚠ 未在 Linux 验证」的条目都还没跑过。

---

## 1. 前置

| 项 | 值 |
| --- | --- |
| Python | ≥ 3.10（本机 Linux 实测 3.14.7） |
| 依赖 | `pip install PySide6 pyinstaller`（本机：PySide6 **6.11.2**、PyInstaller **6.22.3**） |
| 交叉编译 | **不支持**。Windows 的 exe 只能在 Windows 上打（规格 §14/§17 M4） |

Windows 上建议（仓库根目录）：

```bat
py -3 -m venv .venv
.venv\Scripts\pip install -e ".[ui,dev]"      :: 等价于 pip install PySide6 + pytest/pytest-qt
.venv\Scripts\pip install pyinstaller
```

也可以只 `pip install PySide6 pyinstaller`：打包不要求项目被安装（spec 用 `pathex` 指向仓库根目录），
但跑测试需要 `-e ".[ui,dev]"`。

## 2. 打包命令

### 2.0 一键脚本（推荐）

两个平台各有一个文件夹，各放一个一键脚本 —— 建 venv → 装依赖 → 打包 → 产物自检：

| 平台 | 脚本 | 产物 |
| --- | --- | --- |
| Linux | `packaging/linux/build.sh`（`bash packaging/linux/build.sh`，**已在 Linux 实测通过**） | `dist/linux/tu-shell-agent/tu-shell-agent` |
| Windows | `packaging/windows/build.bat`（双击即可，**未在 Windows 上执行过**） | `dist\windows\tu-shell-agent\tu-shell-agent.exe` |

两个脚本共用同一份 spec 与入口（`packaging/tu-shell-agent.spec` + `packaging/entry.py`，
它们与平台无关），只是产物分目录存：`dist/linux/` 与 `dist/windows/` 互不覆盖。

**Windows 那个 .bat 我没有 Windows 机器可以验证**（PyInstaller 也不能交叉编译），
它只是把下面第 1-3 节的命令按顺序抄了一遍。若某一步失败，请照本节逐条手动跑。

> 说明（脚本第 2 步的失败后果分三档，别一刀切）：升级 pip 失败只警告；**PySide6 与
> pyinstaller 失败会停**（没有它们打不出产物）；`pip install -e ".[ui,dev]"` 失败**只警告**
> —— 它只影响"能不能跑 pytest"与控制台入口，而 spec 用 `pathex` 指向仓库根，打包本身
> 不要求项目被安装。实测网络抖动时这一步会因为拉不到构建依赖而失败，那时把整次打包判死
> 是没有道理的（产物其实完全能出）。

> 另：这一步此前在本仓库**必然失败**
> （setuptools 的平铺布局自动发现会因为"发现多个顶层包"拒绝构建：根目录下同时有
> `tu_shell_agent/`、`packaging/`、`test_fixtures/`），已在 `pyproject.toml` 里加
> `[tool.setuptools.packages.find] include = ["tu_shell_agent*"]` 修掉。

### 2.1 手写命令

**必须在仓库根目录执行**：`--distpath` / `--workpath` 是相对当前目录解析的
（spec 内部的路径已经用 `SPECPATH` 拼成绝对路径，所以从别处调用不会把仓库外的目录混进模块搜索路径）。

Linux（本机已验证）：

```bash
.venv/bin/python -m PyInstaller --clean --noconfirm --distpath dist/linux --workpath build/linux packaging/tu-shell-agent.spec
```

Windows（⚠ 未在 Linux 验证）：

```bat
.venv\Scripts\python -m PyInstaller --clean --noconfirm --distpath dist\windows --workpath build\windows packaging\tu-shell-agent.spec
```

产物：`dist/tu-shell-agent/`（one-folder，Linux 实测 **217MB**；Windows 体积会不同）。
注意 spec 里 `console=False`：Windows 上是 GUI 子系统 exe（双击不弹黑窗），命令行读退出码要按第 3 节的办法。

### 2.2 为什么入口是 `packaging/entry.py`，不是计划里写的 `tu_shell_agent/ui/app.py`

计划原文的 spec 把 `tu_shell_agent/ui/app.py` 直接当入口脚本。**实测跑不通**，证据（Linux + PyInstaller 6.22.3）：

```console
$ .venv/bin/python -m PyInstaller --clean --noconfirm --distpath dist --workpath build packaging/tu-shell-agent.spec
INFO: Analyzing /home/vconlln/my-agent/tu_shell_agent/ui/app.py
INFO: Building COLLECT COLLECT-00.toc completed successfully.
INFO: Build complete! The results are available in: /home/vconlln/my-agent/dist
build exit=0                                   # ← 打包阶段不会报错！

$ QT_QPA_PLATFORM=offscreen ./dist/tu-shell-agent/tu-shell-agent --self-test
Traceback (most recent call last):
  File "app.py", line 9, in <module>
ImportError: attempted relative import with no known parent package
[PYI-424735:ERROR] Failed to execute script 'app' due to unhandled exception!
exit=1
```

原因：PyInstaller 把入口脚本当 `__main__` 执行，而 `__main__` 没有 `__package__`，
`app.py` 顶部的相对导入（`from .main_window import MainWindow`）无法解析。
**这个坑在打包阶段是静默的**：构建 exit=0、日志里没有任何 WARNING/ERROR，
只有真正运行产物才会炸——所以改完 spec 必须跑一次 `--self-test`，不能只看"构建成功"。

修法：加一层薄壳入口 `packaging/entry.py`（绝对导入 + `raise SystemExit(main())`），
让真正的界面模块始终以「包内模块」的身份被导入。改完重打，同一份产物 `--self-test` → **exit=0**。

## 3. 打包后自检

Linux（本机实测）：

```bash
QT_QPA_PLATFORM=offscreen ./dist/tu-shell-agent/tu-shell-agent --self-test; echo "exit=$?"
# exit=0，耗时 0.35s（stderr 只有 offscreen 插件自己的
# "This plugin does not support propagateSizeHints()"，那是 QPA 无头插件的常规输出）
```

Windows（⚠ 未在 Linux 验证）：

```bat
dist\tu-shell-agent\tu-shell-agent.exe --self-test
```

期望：**立刻返回 0**（不是"窗口起来了就行"，自检分支不进事件循环，正常应在 1 秒内结束）。

⚠ `console=False` 的 GUI 子系统 exe 在 cmd / PowerShell 里**不会阻塞**，
`%ERRORLEVEL%` / `$LASTEXITCODE` 可能读不到退出码。要真读退出码：

```powershell
$p = Start-Process -Wait -PassThru .\dist\tu-shell-agent\tu-shell-agent.exe -ArgumentList '--self-test'
$p.ExitCode        # 期望 0
```

```bat
start /wait dist\tu-shell-agent\tu-shell-agent.exe --self-test
echo %ERRORLEVEL%
```

需要命令行直接看输出/退出码时，可以临时把 spec 的 `console=False` 改成 `True` 另打一份自检产物
（发布产物保持 `False`，双击才不弹黑窗）。

### 3.1 这个自检到底证明了什么（以及"恰好没报错"的排除）

`--self-test` 会真实构造并 `show()` 主窗口，因此它通过 = **Qt 平台插件真的被收集齐了**
（本机产物里 `_internal/PySide6/Qt/plugins/platforms/` 含 `libqoffscreen.so`（无头自检时加载）
与 `libqxcb.so`；插件缺失时 Qt 会直接 abort，不会有 exit=0）。

两个对照实验，用来排除"恰好没报错"：

| 对照 | 结果 | 说明 |
| --- | --- | --- |
| 产物**不带** `--self-test` 运行 8s | `exit=124`（被 `timeout` 杀掉） | 默认路径确实在跑事件循环；自检的"立刻返回"是开关起的作用，不是进程本来就会退 |
| 计划原文入口的产物 | `exit=1` + `ImportError`（见 2.1） | 失败时一定会以非 0 退出并打出 traceback，所以 exit=0 不是"入口压根没被执行" |

## 4. 打包时的 warning：哪些是噪音，依据是什么

本次构建日志里**一共只有 2 条 WARNING**，其余全是 INFO：

| warning | 结论 | 依据 |
| --- | --- | --- |
| `Unrecognised line of output '缓存生成者：ldconfig (GNU libc) stable release version 2.44' from ldconfig` | 噪音 | `ldconfig -p` 输出的**中文本地化表头**解析不了，只影响"系统库索引"这条可选路径；实际依赖仍由 ldd 解析成功，构建 exit=0、产物自检 exit=0 |
| `ldd warnings for '/usr/lib/libgcc_s.so.1': ldd: 警告：您没有此文件的执行权限` | 噪音 | ldd 对该系统库没有执行权限而告警，但该库**确实进了产物**：`dist/tu-shell-agent/_internal/libgcc_s.so.1` 与 `libstdc++.so.6` 都在 |

另外 PyInstaller 自己的「未找到模块」清单 `build/tu-shell-agent/warn-tu-shell-agent.txt`（66 行）
逐类确认过，**没有真缺失**：

- `_winapi` / `msvcrt` / `winreg` / `nt` / `_overlapped`：Windows 专有，Linux 上本来就找不到
  （在 Windows 上打时这些会消失，反过来缺 Linux 专有的那几个）。
- `_scproxy`：macOS 专有。
- `trio` / `outcome` / `'trio.*'`：anyio 的 trio 后端，本项目只用 asyncio 后端且没装 trio。
- `_typeshed`：只给类型检查器用的桩模块。
- `'collections.abc'`：PyInstaller 对 `collections.abc` 的已知误报（它是 `collections` 的子模块，
  运行时按名字导入即可）。本次产物实测能构造完整窗口、控制器与 httpx 相关模块后正常退出，没有相关 ImportError。

判断"是不是真缺"的唯一可靠办法是**跑产物自检**，不是看清单长短。

关于 `styles/`（规格 §14 点名要求确认 `platforms/`、`styles/`）：本机 Linux 产物里
`_internal/PySide6/Qt/plugins/platforms/` 齐全，但**没有 `styles/` 目录**——这不是打包漏了，
而是 PySide6 的 **Linux wheel 本身就没有** `plugins/styles/`（对照 `.venv/lib/.../PySide6/Qt/plugins/` 同样没有）。
Windows wheel 一般带 `styles/qmodernwindowsstyle.dll`，需在 Windows 侧确认（见第 6 节第 5 条）。

## 5. `--self-test` 为什么这么实现（`tu_shell_agent/ui/app.py`）

- **在 `recheck_environment()` 之前 `return 0`**：那个调用会起子进程探测三件套（规格 §9）。
  放在自检里既慢，又会让"这台机器缺工具"看起来像"打包坏了"，无头/CI 下还可能把冒烟进程拖住。
  `tests/test_app_self_test.py::test_self_test_does_not_probe_environment` 就是钉这条的。
- **优先复用已存在的 `QApplication`**：Qt 是单例，进程里已有实例时再造第二个会直接 `RuntimeError`
  （pytest-qt 会替整个测试会话建一个）。
- **模块级 `_APP` 持强引用**：自己建的 `QApplication` 被 GC 之后再建第二个，进程会崩在退出路径上；
  测试里连着调两次 `main()` 就会走到这一步。

## 6. 规格 §14「只能在 Windows 手测」清单（逐条抄录 + 勾选表）

> 摘自 `docs/superpowers/specs/2026-09-17-tu-shell-agent-design.md` §14，条目文字按原文，
> 「怎么测」一列是本次补充的操作路径；最后一列是本机（Linux）的验证状态。

| # | §14 原文条目 | 怎么测 | Linux 状态 |
| --- | --- | --- | --- |
| 1 | [ ] Git Bash 探测与 `bash.exe --noprofile --norc` 执行；进程树取消（`taskkill /T /F`） | 自检页应列出 Git Bash 路径与版本；跑一个含 `sleep` 的方案后点「取消」，用任务管理器确认 `bash.exe` 整棵树没了 | ⚠ 未在 Linux 验证（Linux 走 POSIX 进程组，代码路径不同） |
| 2 | [ ] shellcheck 探测与 winget 安装指引；UTF-8 输出无乱码 | 临时改名 `tools\shellcheck.exe` 验安装指引；跑一个输出中文的脚本，右栏输出区不应出现 `????` 或 `锟斤拷` | ⚠ 未在 Linux 验证（仅 Linux 的 UTF-8 环境通过） |
| 3 | [ ] opencode 原生安装下的 `serve` 启动、agent 发现（`opencode agent list`）、结构化输出实际可用 | 先 `opencode auth login`（见第 8 节）；跑一次真实生成，确认 `serve` 子进程被拉起、agent 被列出、脚本按契约返回 | ⚠ 未在 Linux 验证（本机 `tools/opencode` 是 1.18.31 但无凭据） |
| 4 | [ ] **权限确实生效**（整个安全模型的地基）：在受控运行目录里让 agent 尝试执行一条无害命令、尝试写一个文件，确认结果是**被拒绝**而不是弹出 `ask` 询问导致挂起；并确认用户全局配置里把 `bash` 设为 `allow` 也覆盖不了本 agent 的 `deny` | 生成阶段观察是否挂住（挂住 = `ask` 没被 deny 覆盖）；检查运行目录里没有多出文件 | ⚠ 未在 Linux 验证；**规则合并那一半已在 Linux + opencode 1.18.31 实测通过**（规格 §18 风险 6），Windows 侧是复核实跑行为 |
| 5 | [ ] PyInstaller 产物：one-folder 目录与单文件便携 exe 双击可用；PySide6 的 Qt 插件（`platforms/`、`styles/`）被正确收集，界面能起来 | 双击 `dist\tu-shell-agent\tu-shell-agent.exe`；再确认 `_internal\PySide6\Qt\plugins\platforms\qwindows.dll` 与 `styles\qmodernwindowsstyle.dll` 存在 | ⚠ 未在 Linux 验证（Linux 侧 one-folder + `--self-test` exit=0 已过；`styles/` 见第 4 节；**单文件 exe 本次未产出**，见下） |
| 6 | [ ] 中文路径与含空格路径（`C:\Users\张三\我的 方案.md`） | 把方案复制到这类路径下跑一次完整运行，看运行目录、日志、`meta.json` 是否都正常 | ⚠ 未在 Linux 验证 |

补充项（不在 §14 原文里，但打包验收会用）：

| # | 补充条目 | 怎么测 | Linux 状态 |
| --- | --- | --- | --- |
| 7 | [ ] 打包产物自检 | `Start-Process -Wait -PassThru ... -ArgumentList '--self-test'` 的 `ExitCode` 应为 0（见第 3 节） | ✅ 已完成（Linux `--self-test` → exit=0） |
| 8 | [ ] 单文件便携 exe | 本次只按计划出了 one-folder。要单文件另跑：`pyinstaller --onefile --clean --noconfirm --distpath dist-onefile --workpath build-onefile packaging\tu-shell-agent.spec`（spec 里的 `exclude_binaries=True` 需一并去掉，或另写一份 one-file spec）；单文件启动会先解包到临时目录，**启动慢是正常的** | ⚠ 未实现、未验证 |
| 9 | [ ] 应用退出后无残留进程（规格 §15.7） | 跑完一次运行后关窗，任务管理器里不应残留 `opencode.exe` / `bash.exe` | ⚠ 未在 Linux 验证 |

### 6.1 外观相关（2026-09-19 新增功能，代码平台无关，但要在 Windows 上过一眼）

界面全部是 Qt/QSS + 纯 Python，没有一处平台分支，所以下面这些在 Windows 上**应当**与 Linux 一致；
但"应当"不等于"验过"，逐条手测就能收口：

| # | 条目 | 怎么测 | Linux 状态 |
| --- | --- | --- | --- |
| A1 | [ ] 半透明（`背景效果 = 半透明`）在 Windows 上真的透 | 关掉其它窗口只留桌面，切到「半透明」，应能看到壁纸透出来；文字仍清晰 | ✅ Linux 已验（合成不透明度：空白 43% / 内容区 71%） |
| A2 | [ ] 亚克力（`自绘壁纸，不需要系统支持`）能找到 Windows 壁纸 | 切到该模式，设置页提示应显示壁纸文件名（注册表 `HKCU\Control Panel\Desktop\Wallpaper`；聚焦/幻灯片时回退到 `%APPDATA%\Microsoft\Windows\Themes\TranscodedWallpaper`） | ⚠ Windows 探测分支本机跑不到；解析逻辑与回退路径已用假 home 单测覆盖 |
| A3 | [ ] 亚克力模糊层跟窗口尺寸走 | 拖动窗口边缘连续改大小，背景不应卡顿或残留旧尺寸的糊图（结果按 64px 分桶缓存） | ✅ Linux 已验（1400×900 生成 0.07s） |
| A4 | [ ] 亚克力模式下列表/菜单/提示不透明、字看得清 | 打开「界面字体」下拉，滚动列表；长文本/中文都要能读 | ✅ Linux 已验（弹层对比度 13:1；修复前 1.7:1） |
| A5 | [ ] `背景效果 = 亚克力模糊（问系统要）` 在 Win11 22H2+ 生效 | 切过去后窗口背后应被 DWM 模糊（Acrylic）；Win10 会如实提示"当前桌面不支持…已退化为半透明" | ⚠ 未验证（DWM 调用只在 Windows 上执行） |
| A6 | [ ] 界面缩放与字体在多 DPI 显示器上正常 | 100% / 150% / 200% 缩放下各看一遍；「界面缩放」0.8~1.6 各试一档 | ⚠ 未验证（Linux 侧只验了缩放值本身） |
| A7 | [ ] 圆角与配色一致（按钮/页签条/卡片/输入框） | 对照 Linux 截图看四个地方：页签条整条圆角、按钮圆角、输入框圆角、列表圆角 | ✅ Linux 已验（每个都有像素级用例） |
| A8 | [ ] 中文字体不糊、不缺字 | 界面字体选「系统默认」与「微软雅黑」各看一遍；`✓ ⚠ ▸` 这类符号不应显示成方块 | ⚠ 未验证（Windows 字体回退与本机不同） |

## 7. 本机（Linux）明确没验证的项

- Windows exe 的**双击启动**、GUI 子系统 exe 的退出码读取（第 3 节的 `Start-Process` 写法是通用做法，本机没法实测）。
- `taskkill /T /F` 杀进程树的真实行为（含是否漏杀孙进程）。
- 中文路径 / 含空格路径（`C:\Users\张三\我的 方案.md`）。
- Windows 控制台下的 UTF-8 输出（代码页 / 乱码）。
- Windows 上 opencode 原生安装的 `serve`、`agent list`、结构化输出、权限 `deny` 实跑。
- Windows wheel 的 `styles/` Qt 插件是否被收集。
- 单文件便携 exe（本次未产出）。
- Windows 产物体积（Linux 是 217MB）。

## 8. 环境提醒：无凭据时"生成本身会失败"，别误判成打包问题

本机 `tools/opencode` 是 **1.18.31**，但 `opencode auth list` 显示 **0 credentials**；
免费额度会拒绝"把工具全部 deny 的 agent"，所以**没有凭据时生成阶段必然失败**。
这与打包无关（打包自检 `--self-test` 不碰 opencode），但 Windows 手测第 3、4 条之前必须先：

```bat
opencode auth login
```

否则手测会卡在"第一次生成就失败"，看起来像打包坏了。
