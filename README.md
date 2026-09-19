# shell-agent

把一份**方案文档**变成一份**可执行的 shell 脚本**：模型只负责写，后端独占执行与校验，失败证据回灌给模型自修。

面向的场景是"我有一段用自然语言描述的操作（清理日志、批量转码、按规则归档……），懒得手写脚本，又不放心让模型直接在我机器上跑命令"。

```
方案文档 ──► opencode 生成脚本 ──► shellcheck 校验 ──► 人工确认 ──► bash 执行
                    ▲                                                  │
                    └──────────── 失败证据回灌（≤3 轮）◄───────────────┘
```

## 设计上的三条硬规则

1. **模型只写不跑。** 每次运行都在运行目录里生成一份专用 agent 配置，把 `bash` / `edit` / 网络等能力逐项 deny，只留对本次运行目录的只读白名单。模型看不到执行结果，只拿到失败证据的文本。
2. **引擎独占执行。** 执行、校验、落盘都在后端（`orchestrator`）里做，界面只挑入口、给参数 —— 界面里不许再写一遍"先校验再执行"，否则规则迟早漂移。
3. **`succeeded` 只代表「shellcheck 无阻断项 + 退出码 0」**，**不代表**脚本实现了方案里的约束。所以模型的 `notes` 与 `assumptions` 会和脚本正文并排显示，最终判断留给人。

## 装与跑

```bash
git clone https://github.com/vconlln/shell-agent && cd shell-agent
python3 -m venv .venv
.venv/bin/python -m pip install -e ".[ui,dev]"
```

前置三件套（缺一不可，程序启动时会自检）：`opencode`、Git Bash（Windows）/ bash（Linux）、`shellcheck`。
仓库**不附带**这些二进制：本机开发时放在 `tools/` 下（已在 `.gitignore` 里），
也可以通过设置页写绝对路径，或放进 PATH。

```bash
# 桌面界面
.venv/bin/python -m tu_shell_agent.ui.app

# 无界面驱动（开发/排查用）
.venv/bin/python -m tu_shell_agent.cli --plan test_fixtures/plan-simple.md \
    --template single --run-root /tmp/tu-runs --yes
```

Windows 上的 exe：把仓库拷过去，双击 `packaging\windows\build.bat`（或照 `packaging/build.md` 手动跑），产出 `dist\windows\tu-shell-agent\tu-shell-agent.exe`。Linux 用 `bash packaging/linux/build.sh`。

## 界面

设置页里有「外观」分组：**界面缩放**（0.8–1.6×，字号与尺寸一起缩放）、**界面字体**、
**等宽字体**（脚本/报告/输出用）、**背景效果**（不透明 / 半透明 / 亚克力模糊）。
模糊由窗口管理器提供：Windows 11 上是系统原生 Acrylic，Wayland（含 Niri）拿不到 —— 那时会
**如实提示**并退化为半透明，不会假装成功。

深色主题照 Codex 桌面版的设计令牌做（`#181818` 底、`#212121` 面板、6/8px 圆角、等宽字体用于
脚本与报告），令牌来源与实现要点见 `docs/superpowers/specs/2026-09-19-tu-shell-agent-codex-dark-theme-design.md`。

三栏 + 底栏：左栏是方案与本次运行参数（上）/ 模板库（下），中栏是带行号的脚本视图、与上一轮的 diff、轮次时间线，右栏是 shellcheck 报告（按编号分组、标出哪些会阻断）、执行输出（stderr 标红）、模型取舍说明。底栏是历史运行（可回放）与环境自检 / 设置两个页。

底部按钮：开始、取消、继续修复（复用原会话接着修）、改后重跑（只重跑校验与执行，不烧生成轮次）、打开运行目录。

## 测试

```bash
.venv/bin/python -m pytest -o addopts=""
```

| 环境 | 结果 | 说明 |
| --- | --- | --- |
| 装了 shellcheck | 223 passed, 1 skipped | 本机（把 shellcheck 放进 `tools/` 或 PATH） |
| 干净克隆、没装 shellcheck | 217 passed, 7 skipped | 6 条 shellcheck 用例自报"找不到 shellcheck"后跳过，不造假 |

剩下 1 条 skip 是需要真实 opencode 与凭据的端到端用例（`TU_LIVE=1` 才跑）。

## 已知限制（如实写）

- **生成需要 opencode 已登录**（`opencode auth login`）。仅靠免费额度时，上游会拒绝"把工具全部 deny 的 agent"，表现为每轮生成都失败 —— 这是安全模型的代价，不会为了跑通去放宽权限。失败时界面会把落盘的错误证据（原始报错）摊在输出区，并标明它来自哪个文件。
- **Windows 侧的 exe 打包与手测清单尚未执行**：`packaging/build.md` 第 6 节把要验的项逐条列成了勾选表（`taskkill /T /F`、中文与含空格路径、UTF-8 输出、原生 `opencode serve`、权限 deny 实测等）。
- 左栏的**拖入方案**与**方案摘要**没有实现（规格里有，实现里没有）。
- 方案正文与执行输出**没有体积上限**：误选一个几 MB 的文件会卡一下界面（8MB 实测约 2 秒）。
- "改后重跑"与引擎**共用同一轮的产物目录**，用户手改的脚本会覆盖引擎那一轮的证据（`shellcheck.json` / `stdout.txt` 等），meta 里也没有"这次是 verify"的标记。
- 时间线不含耗时；`duration_ms` 只在 succeeded 的 meta 里写。

## 仓库结构

```
tu_shell_agent/
  orchestrator/       编排：轮次循环、契约校验、失败证据、终态落盘（不 import 界面/网络/子进程）
  shell_toolchain/    shellcheck 调用与脚本执行（唯一执行者）
  opencode_adapter/   驱动 opencode serve：会话、结构化输出、SSE 事件、权限拒绝
  template_store/     模板库与占位符渲染
  run_store/          运行目录布局与产物落盘
  ui/                 PySide6 界面（三栏 + 历史回放 + 设置）
  cli.py              无界面驱动
docs/
  superpowers/specs/  设计规格
  superpowers/plans/  实现计划（分引擎与界面两份）
  audit/              交付审计台账（含未修项与原因）
packaging/            两个平台各一个一键打包脚本 + 规格 + Windows 手测清单
tests/                211 条测试（引擎、适配器离线契约、界面无头测试）
```

## 状态

引擎（Plan 1）与界面（Plan 2）都已实现并通过测试；两份计划末尾都列了完成标准，`docs/audit/` 里记录了逐任务独立评审发现并修掉的问题，以及**没有修**的部分及原因。
