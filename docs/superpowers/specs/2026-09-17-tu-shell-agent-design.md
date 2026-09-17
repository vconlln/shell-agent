# tu-shell-agent 设计规格

- 状态：设计已批准；**技术栈已于 2026-09-17 修订为「全 Python」（见 §4 与 §19）**
- 日期：2026-09-17
- 目标平台：Windows（打包为本地 exe），开发/单测在 Arch Linux 上进行

## 1. 背景与目标

在 Windows 上，把"方案文档 → 可执行 shell 脚本"这件事做成一个带界面的本地应用：后端调用本机已安装的 opencode 生成脚本，用 Windows 原生 Git Bash 执行并捕获输出，再用 shellcheck 做静态校验；校验或执行失败时，把结构化证据回灌给同一个 opencode 会话，让它自己修，最多 3 轮，超限转人工。

**成功标准**

1. 一次运行全程可见：每轮脚本全文（与上轮 diff）、shellcheck 报告（按 SC 编号分组、可跳行）、执行的 stdout/stderr、退出码、耗时、轮次时间线。
2. 模板可复用、可编辑、可保存多套，模板骨架不被模型改写。
3. 三个外部依赖（opencode / Git Bash / shellcheck）缺失时给出明确诊断与安装指引，绝不静默降级。
4. **执行权 100% 在后端**：opencode 侧被配置为既不能执行命令，也不能写文件。
5. 单测可在 Linux 上跑；Windows 专属部分有一份可执行的手测清单。

**明确不要**：黑盒进度条；看不见脚本内容；报错只躺在日志里。

## 2. 范围

**做**：单方案 + 单模板 → 单脚本；3 轮自修；本地单机；opencode 为**Windows 原生**安装；运行历史本地留存与回放。

**不做**（YAGNI，见 §15）：批量多脚本；远程/多用户；git 集成与自动提交；模板市场；模型与供应商配置界面；WSL 内 opencode 的路径映射。

## 3. 已验证的外部事实

这些事实决定了下面的设计，均为一手文档，实现时不需再猜。

**opencode**

- CLI 非交互运行：`opencode run [message..]`，支持 `--agent`、`--dir`（指定运行目录）、`--session/-s`（续跑会话）、`--continue`、`--format json`（原始 JSON 事件）、`--auto`（自动批准未被显式拒绝的权限）、`--attach <url>`（复用已运行的 server）。[CLI 文档](https://opencode.ai/docs/cli/)
- 常驻 server：`opencode serve`（默认 `127.0.0.1:4096`），可用 `OPENCODE_SERVER_PASSWORD` + `OPENCODE_SERVER_USERNAME`（默认 `opencode`）启用 HTTP Basic 认证；暴露 OpenAPI 3.1（`GET /doc`）、健康检查 `GET /global/health`（返回 `{healthy, version}`）、SSE 事件流 `GET /event`。[Server 文档](https://opencode.ai/docs/server/)
- **官方 SDK 只有 JS/TS**（`@opencode-ai/sdk`，`createOpencode()` / `createOpencodeClient({baseUrl})`）。**Python 没有官方 SDK** → 本应用直接打 server 的 HTTP/SSE 接口，端点契约以 server 暴露的 OpenAPI 3.1（`GET /doc`）为准。[SDK 文档](https://opencode.ai/docs/sdk/)
- **结构化输出**（已用真实 opencode **1.18.31** 的 OpenAPI 核对）：`POST /session/{sessionID}/message` 的 body 字段**就是 `format`**，类型为 `OutputFormat = TextOutputFormat | OutputFormatJsonSchema`，后者是 `{type:'json_schema', schema, retryCount?}`；结构化结果落在响应的 `AssistantMessage.structured`（JS SDK 文档写作 `info.structured_output`，实现里两个都读）；失败时 `info.error` 的**确切形状**是 `{"name": "StructuredOutputError", "data": {"message": ..., "retries": ...}}`（`name` 与 `data` 均为 required；`message` **嵌在 `data` 里**，不在顶层）。请求体只有 `parts` 必填，`agent` / `model` / `system` / `tools` 均可选。只有 server 提供该能力，CLI 没有对应 flag。[SDK 文档](https://opencode.ai/docs/sdk/#structured-output)
- Agent 定义：markdown + frontmatter 放在全局 `~/.config/opencode/agents/` 或项目级 `.opencode/agents/`，**文件名即 agent 名**；也可写在 `opencode.json` 的 `agent` 键下。frontmatter 支持 `description`（必填）、`mode: primary|subagent|all`、`model`、`temperature`、`permission`、`steps`、`disable`、`hidden` 等。[Agents 文档](https://opencode.ai/docs/agents/)
- 非交互生成 agent：`opencode agent create --path <dir> --description <d> --mode <m> --permissions <list> [--model]`，四个参数齐全即不进入交互；`--permissions` 可取值 `bash,read,edit,glob,grep,webfetch,task,todowrite,websearch,lsp,skill`，**未列出的权限一律拒绝**。[CLI 文档](https://opencode.ai/docs/cli/#create)
- 权限模型：取值 `allow | ask | deny`；支持对象式细粒度规则（按输入匹配，`*`/`?` 通配，**最后一条匹配的规则生效**）；键包括 `read, edit, glob, grep, bash, task, skill, lsp, question, webfetch, websearch, external_directory, doom_loop`；默认大多为 `allow`，`doom_loop` 与 `external_directory` 默认 `ask`，`.env` 默认拒绝。**agent 的权限与全局配置合并，且 agent 规则优先**。[Permissions 文档](https://opencode.ai/docs/permissions/)
- 事件流是 **SSE**：`GET /event`（首个事件 `server.connected`）与 `GET /global/event`；信封为 `{id, type, properties}`。**事件名已用 1.18.31 的 OpenAPI 核对**：增量文本走 **`message.part.delta`**（`properties = {sessionID, messageID, partID, field, delta}`，只认 `field == "text"`），而 `message.part.updated` 带的是**整个** `part`（累计文本，只能当不支持 delta 的版本的回退，混用会重复）；权限询问是 **`permission.asked`**（`properties = {id, sessionID, permission, patterns, metadata, always, tool?}`）——**旧文档写的 `permission.updated` 在 1.18.31 的事件联合里根本不存在**。→ §12 的真流式有据可依。
- 权限询问**可以编程回答**：`POST /session/{sessionID}/permissions/{permissionID}`，body `{response: 'once' | 'always' | 'reject'}`；配合 `permission.asked` 事件可在 UI 侧接管。→ 见 §7.2 的自动拒绝安全网。
- `opencode run` **从不发送**结构化输出所需的 `format`（源码确认），所以 schema 强制只能走 server/SDK。另外 `run` 的退出码只有 `1` 且官方未文档化 → 适配器**不得用退出码判断成败**，一律以事件与 health 为准。
- Agent 发现：每个配置目录下按 `{agent,agents}/**/*.md` 递归查找，项目级 `.opencode/agents/`（推荐，复数）与 `.opencode/agent/`（单数，向后兼容）都可用；文件名即 agent 名。
- 版本与安装：当前为 `opencode-ai@1.18.31`（npm 与 GitHub release 同版本号）；官方列出的 Windows 安装方式为 `choco install opencode`、`scoop install opencode`、`npm i -g opencode-ai`、`mise`（**官方未列 winget**），release 另附 `opencode-windows-x64.zip` 独立包。
- Windows：官方明确 **"While OpenCode can run directly on Windows, we recommend using WSL"**，Windows 页主要讲 WSL 接法。[Windows 文档](https://opencode.ai/docs/windows-wsl/) → 见 §18 风险与 §9 自检。

**shellcheck**

- 输出格式 `-f json1`：`{comments:[{file,line,column,level,code,message}]}`。[手册](https://man.archlinux.org/man/shellcheck.1.en)
- 退出码：`0` 无问题；`1` 有问题；`2` 文件无法处理；`3` 调用语法错（未知参数）；`4` 选项错（未知 formatter）。**`1` 不是失败**，状态机必须区分这三类。
- `--norc` 避免读取脚本目录及父目录的 `.shellcheckrc`；`SHELLCHECK_OPTS` 环境变量会被隐式前置到命令行 —— 两者都影响结果可复现性，必须显式处理。
- `-S/--severity` 只过滤报告内容；`-s bash` 强制方言（Git Bash 就是 bash）。
- Windows 已知坑：终端非 UTF-8 时会出现 `commitBuffer: invalid argument (invalid character)`。
- Windows 安装：winget 包 id `koalaman.shellcheck`，或官方 GitHub release 的 zip。

## 4. 技术选型

| 项 | 选择 | 理由 |
| --- | --- | --- |
| 语言 | Python 3.14（最低 3.10） | 用户裁定的技术栈；PySide6 的 abi3 轮子覆盖 3.10+，本机 3.14.7 实测可解析 |
| 界面 | PySide6 6.11（Qt 6） | 原生桌面控件；一套栈直接打 exe，不引入 Node；引擎跑在 `QThread`，用 Qt 信号驱动 |
| opencode 接入 | 自管 `opencode serve` + `httpx` 直打 HTTP/SSE | 官方无 Python SDK（§3），端点以 OpenAPI 为准；见 §7.1 |
| 子进程 | `subprocess`（POSIX 用进程组、Windows 用 `taskkill /T /F` 杀树） | 执行脚本、杀进程树、起停 server |
| 打包 | PyInstaller 6.x（one-folder + 单文件便携 exe） | Windows 双击即用；PySide6 有官方 hook |
| 测试 | pytest 9 + pytest-qt 4.5 | 引擎纯 pytest；界面用 pytest-qt |
| 数据与类型 | 冻结 `dataclass` + `typing.Protocol`（不引入 pydantic） | 端口用 Protocol，数据用 dataclass，YAGNI |
| 模板引擎 | 自实现简单替换（`{{name}}`） | 见 §8，YAGNI |

## 5. 架构与组件

单向依赖：`ui → orchestrator → {opencode_adapter, shell_toolchain, template_store, run_store}`。**`orchestrator` 与两个 store 是纯 Python：不 import PySide6、不 import `subprocess`**，外部世界一律通过 §5.1 的端口注入——因此它们能在没有界面、没有 Windows 的机器上纯单测。

| 组件 | 职责 | 依赖 |
| --- | --- | --- |
| `ui`（PySide6） | 主窗口、三区视图、设置页、环境自检页；只发命令、只渲染事件，不含业务判断 | PySide6、`orchestrator` |
| `orchestrator` | 运行状态机（§6）。输入=方案+模板+配置，输出=事件流；唯一"知道流程"的地方 | 仅 §5.1 的端口 |
| `opencode_adapter` | 唯一与 opencode 通信处。对外：`start(run_dir, agent_name, model)` → `session_id`；`generate(session_id, message, schema, ...)` → `GeneratedScript`；`abort()`；`dispose()` | `httpx`、`subprocess` |
| `shell_toolchain` | `detect()` 环境自检；`shellcheck(path)` → 归一化报告；`execute(path, ...)` → 流式输出 + 退出码；`kill_tree(pid)` | `subprocess` |
| `template_store` | 模板 CRUD、占位符元数据、`trusted` 标记、持久化 | `pathlib`、`json` |
| `run_store` | 运行目录布局、每轮落盘、历史索引与回放 | `pathlib`、`json` |
| `cli.py` | 无界面的开发驱动：跑完整流程并打印时间线（Plan 1 的端到端入口） | 以上全部 |

### 5.1 端口（`ports.py`）

四个 `typing.Protocol` 是引擎与外界唯一的缝：

- `ToolchainPort`：`detect()`、`shellcheck(path)`、`execute(path, cwd, timeout_ms, signal, on_stdout, on_stderr)`
- `OpencodePort`：`start(run_dir, agent_name, model)`、`generate(session_id, message, schema, timeout_ms, on_delta, signal)`、`abort(session_id)`、`dispose()`
- `ConfirmPort`：`confirm(round, script_path, script, trusted) -> bool`
- `RunStorePort`：`run_dir`、`write_script(round, script)`、`write_attempt(round, files)`、`write_meta(patch)`

所有跨模块数据在 `types.py` 里以冻结 `dataclass` 定义（`ShellcheckFinding`、`ExecuteResult`、`ContractResult`、`FailureEvidence`、`RunConfig`、`RunEvent`…）：`types.py` 是类型的唯一来源。`opencode_adapter` 的接口刻意与传输方式解耦，端点在 1.x/2.x 之间漂移时只需改这一个模块（§18 风险 9）。

## 6. 状态机与数据流

```
idle
 └─ 载入方案 + 选模板 → 渲染预演（占位符替换 → 骨架）
     └─ 预检（§9 环境自检）
         └─ round = 1
             ├─ generate：opencode 返回 {script, notes, assumptions}
             ├─ 写盘（LF 归一化）→ 契约校验（锚点齐全、非空、≤64KB）
             ├─ shellcheck：按阻断级别判定
             ├─ 执行确认（trusted 模板自动通过）
             ├─ 执行（Git Bash，超时/可取消）
             └─ 判定
                 ├─ 通过 → succeeded
                 └─ 失败且 round < 3 → 回灌证据，round++ → 回到 generate
                    失败且 round == 3 → needs_human
```

- 终止态：`succeeded`、`needs_human`、`cancelled`、`aborted_dependency`（依赖中途损坏）。
- 每轮独立落盘（§10），因此 `needs_human` 时用户能看到全部证据；支持"手工改脚本 → 只重跑 shellcheck/执行"和"从第 n 轮继续让模型修"。
- 同一轮内不允许并发动作：上一轮结束（含取消完成）才解锁下一步。

## 7. opencode 集成契约

### 7.1 传输

**v1 主路径**：每次运行启动一个独占的 `opencode serve`
- `cwd = 本次运行目录`（§10），随机空闲端口，随机 `OPENCODE_SERVER_PASSWORD`，`--hostname 127.0.0.1`。
- 由本应用用 `subprocess.Popen` 拉起（cwd = 运行目录），把 server 的 stdout/stderr 收进 `server.log` 并在自检页展示。
- 客户端：`httpx` + Basic 认证头（用户名 `opencode`，密码 = 我们自己指定的 `OPENCODE_SERVER_PASSWORD`）。
- 用到的端点（Python 无官方 SDK，直接打 HTTP）：`GET /global/health`（就绪判定）、`POST /session`（建会话）、`POST /session/:id/message`（发消息 + `format` 结构化输出）、`GET /event`（SSE）、`POST /session/:id/abort`、`POST /session/:id/permissions/:permissionID`（自动拒绝）。
- 运行结束（含取消/崩溃）必须 `dispose()` 并杀掉 server 进程树。

**为什么不是"CLI 优先"**：结构化输出（§7.3）只有 server 提供，而它正是产出契约可靠性的来源——`opencode run` 从不发送 `format`（源码确认）。

**应急路径**（仅当 Windows 实测 `serve` 不可用时启用，不在首次交付范围）：改用 `opencode run --dir <runDir> --agent <name> --format json -s <sessionId>`，产出契约退化为"文件契约"（agent 获得 `edit` 权限，直接把 `script.sh` 写到运行目录，权限仍保持 `bash: deny`）。切换成本被限制在 `opencode-adapter` 一个模块内。

### 7.2 Agent 定义

每次运行生成一份项目级 agent 定义，**不写用户全局配置**，路径 `<runDir>/.opencode/agents/tu-shell-writer.md`：

```markdown
---
description: Fills a shell script template from a plan document. Writes nothing, runs nothing.
mode: primary
permission:
  "*": deny
  read:
    "*": deny
    "<runDir>/**": allow
  external_directory: deny
---
（提示词见 §7.4）
```

- 权限语义直接利用官方规则：**`"*": deny` 打底 + agent 规则优先于全局**，因此用户全局即使设了 `bash: allow` 也覆盖不了本 agent；`edit`/`bash`/`webfetch`/`websearch`/`task` 全部落进 `deny`。
- `external_directory: deny` 是必需的：它默认为 `ask`。而"不问"这件事不能只靠配置正确 —— 适配器必须订阅事件流，**对任何权限询问（1.18.31 的事件名是 `permission.asked`，同时兼容旧名 `permission.updated`）一律回 `reject`**（`POST /session/{sessionID}/permissions/{permissionID}`，body `{response:'reject'}`），并在 UI 上标注"已自动拒绝"。这样即使规则写漏，运行也只会被拒绝，不会静默挂住。
- 绝对路径（`<runDir>`）在生成时填入，所以 `read` 被锁死在本次运行目录内。
- **不写 `model` 字段**：按官方语义，未指定时 primary agent 使用用户全局配置的模型，这样应用不硬编码也不覆盖用户的供应商设置；设置页可选地固定 `provider/model`，取值来自 `opencode models` 校验。
- 运行前用 `opencode agent list`（cwd = runDir）**验证该 agent 已被发现**；未发现则判 `aborted_dependency` 并展示目录内容与命令输出，不做静默回退。

### 7.3 产出契约（结构化输出）

发消息时带 JSON schema（HTTP：`POST /session/:id/message` 的 body 里带 `format`）：

```js
{
  type: 'json_schema',
  retryCount: 2,
  schema: {
    type: 'object',
    additionalProperties: false,
    required: ['script', 'notes', 'assumptions'],
    properties: {
      script:      { type: 'string', description: '完整的 shell 脚本内容；必须保留模板锚点注释；LF 换行；不得包含 markdown 代码块围栏' },
      notes:       { type: 'string', description: '做了哪些取舍；方案含糊处如何处理' },
      assumptions: { type: 'array', items: { type: 'string' }, description: '你假定的前提' },
    },
  },
}
```

后端拿到 `script` 后**自己写盘**。这带来两个直接收益：opencode 不需要 `edit` 权限；"它没写出文件"这一整类失败被消除。`StructuredOutputError` 计一次契约失败并回灌。

### 7.4 提示词契约

agent 正文（system）要点：
- 角色：把方案填进给定模板骨架，产出一个可直接执行的 bash 脚本。
- 硬规则：保留全部模板锚点注释；保持模板整体结构；不引入网络下载、提权、`sudo`、`curl | bash`；不使用交互式命令；换行 LF；只在返回 JSON 的 `script` 字段里给出脚本。
- 方案含糊时选择保守实现，并写进 `assumptions`/`notes`，不要静默猜测。

每轮 user message：
- 第 1 轮：模板骨架全文（含锚点）、方案全文、运行目录绝对路径、契约说明。
- 第 n 轮（n≥2）：上述内容的引用 + **结构化失败证据**（§7.5）+ "只修这些问题，保持锚点与结构不变，返回完整脚本"。

### 7.5 失败证据格式（回灌）

不倾倒原始日志，而是归一化证据：

```json
{
  "round": 2,
  "stage": "shellcheck | execute | contract",
  "contract": { "reason": "missing_anchor", "missing": ["@@TU:BODY@@"] },
  "shellcheck": [
    { "code": "SC2086", "line": 12, "column": 5, "level": "warning", "message": "Double quote to prevent globbing and word splitting." }
  ],
  "execute": { "exitCode": 1, "timedOut": false, "durationMs": 842,
               "stdoutTail": "…最后 40 行…", "stderrTail": "…最后 40 行…" }
}
```

### 7.6 会话与超时

- 会话：`session.create({title: runId})` 一次，后续轮复用同一 `sessionId`（token 连续、模型能看到自己上一版脚本）。
- 每轮 `generate` 超时默认 300s（可配）；超时 → `session.abort()` + 计一次失败轮。
- 会话上下文膨胀由 opencode 自身机制处理，应用不干预。

## 8. 模板系统

- 存储：`%APPDATA%\tu-shell-agent\templates\`，`index.json` 记录 `{id, name, description, placeholders:[{name, default, description}], trusted, updatedAt}`，模板正文为 `<id>.tpl.sh`。
- 占位符：`{{name}}` 与 `{{name:默认值}}`，**只做字符串替换，不引入模板引擎**。渲染时遇到未在元数据声明的占位符 → 直接报错（不静默留空）；渲染结果在开始前给用户预演。
- 锚点：每套模板正文含一个或多个 `# @@TU:<NAME>@@` 标记，契约校验要求生成结果里全部存在，缺失即 contract 失败。
- `trusted` 标记：仅影响"执行前是否弹确认"，不影响 shellcheck 与锚点校验。
- 内置 3 套（首次启动写入，之后归用户所有）：
  1. **单命令执行**：shebang + `set -euo pipefail` + `main()` + 锚点。
  2. **参数解析批处理**：`getopts` 骨架 + `usage()` + 参数校验 + 锚点。
  3. **带日志与错误处理**：`log()`/`die()`、`trap ... ERR`、`mktemp -d` + 退出清理 + 锚点。

## 9. 环境自检（`shell-toolchain.detect()`）

启动即跑，结果呈现在"环境自检"页，可一键重测。任一必需项缺失 → 明确报错 + 指引，不降级。

| 项 | 探测顺序 | 失败指引 |
| --- | --- | --- |
| opencode | 设置项 → `where opencode` → `where opencode.cmd` → `%APPDATA%\npm\opencode.cmd` → 用户手动指定；再 `opencode --version` 与 `GET /global/health` 的 `version` 交叉确认；要求 ≥ 1.1.1（`permission` 取代旧 `tools` 的版本），低于则警告 | 提示安装/升级，给出官方 Windows 方式：`choco install opencode` / `scoop install opencode` / `npm i -g opencode-ai`（官方未列 winget），或 release 的 `opencode-windows-x64.zip`；明确告知本应用要求 **Windows 原生** opencode，若只在 WSL 内检测到则给出说明 |
| agent 可见性 | cwd=runDir 执行 `opencode agent list`，确认 `tu-shell-writer` 在列 | 展示命令原始输出与 `.opencode/agents` 目录内容 |
| Git Bash | 设置项 → `%ProgramFiles%\Git\bin\bash.exe` → `%ProgramFiles(x86)%\Git\bin\bash.exe` → `where bash`；记录 `bash --version` | 指向 Git for Windows 安装 |
| shellcheck | 设置项 → `where shellcheck` → 常见安装位置；记录 `shellcheck --version` | `winget install --id koalaman.shellcheck`，或官方 GitHub release zip；允许手动指定 exe 路径 |

自检结果（各组件版本与绝对路径）写入每次运行的 `meta.json`，便于事后定位"换机器就失败"。

## 10. 磁盘布局

```
%APPDATA%\tu-shell-agent\            # Electron userData
  settings.json                      # 运行根目录、阻断级别、轮次上限、超时、各组件路径
  templates\index.json, <id>.tpl.sh
  runs-index.json                    # 历史列表（指向运行目录，可重建）

<运行根目录>\<runId>\                 # 默认 %USERPROFILE%\tu-shell-runs\<runId>
  .opencode\agents\tu-shell-writer.md
  plan.md                            # 方案副本（冻结本次输入）
  template.sh                        # 渲染后的骨架（只读参考）
  script.sh                          # 当前脚本（LF）
  attempts\1\{script.sh, shellcheck.json, shellcheck.txt, stdout.txt, stderr.txt, evidence.json}
  attempts\2\...
  server.log                         # opencode serve 的 stdout/stderr
  meta.json                          # 输入摘要、配置快照、自检结果、轮次结论、sessionId
```

`runs-index.json` 只是索引，删掉可重建；运行目录本身自包含，可整体拷走归档。

## 11. 执行与校验层

**shellcheck 调用（固定参数）**

```
shellcheck --norc -s bash -f json1 -- <script>
```

- 环境变量中**删除 `SHELLCHECK_OPTS`**，否则用户环境的默认 flag 会污染结果。
- 不加 `--severity`：拿全量报告，阻断判定在应用内做。
- 退出码处理：`0` 无问题；`1` 有问题（读 `json1` 报告）；`2` 文件无法处理 → 依赖错误；`3`/`4` → 我们的调用 bug，报错并附完整命令。
- 阻断级别默认 `info`（error/warning/info 触发回灌修复），`style` 只展示；可配。**为什么不是 `warning`**（实测）：最常见的 SC2086（变量未加引号）在 shellcheck 里是 **info** 级，用 `warning` 当默认会让这类真实隐患「只展示、不修」，脚本带着隐患去执行——那正是本应用要消除的失败方式；而 `style` 才是真正的吹毛求疵层。

**执行**

```
<git>\bin\bash.exe --noprofile --norc <runDir>\script.sh
```

- 工作目录默认 = 运行目录，确认页可改。
- stdout/stderr 分别流式回传并落盘；超时默认 120s（可配）；取消时用 `taskkill /PID <pid> /T /F` 杀整棵进程树。
- 输出按 UTF-8 解码；遇非法字节用有损解码并在 UI 标注（避免 Windows 代码页导致的乱码假象）。
- 每次执行前展示脚本全文确认；模板标记 `trusted` 则跳过（§8）。确认页对危险模式（`rm -rf`、`mkfs`、`dd`、`> /dev/sd*`、`chmod -R 777 /`、`curl|bash`）高亮提示。
- 换行：写入时强制 LF（生成结果里的 CRLF 归一化），并在执行前校验文件无 `\r`。
- **`succeeded` 的语义边界（实测教训，务必写进 UI）**：`succeeded` 只表示「shellcheck 通过 + 脚本执行退出码 0」，**不表示方案里的约束被实现了**。三次真实运行里，模型三次都靠**放宽**方案的「目录不存在时以非零退出码结束」约束而通过（分别是：报 0 个文件并退出 0、直接报 0 个、自己 `mkdir -p logs` 再报 0 个），而它把这些取舍写在结构化输出的 `notes` / `assumptions` 里。因此：① **`attempts/<n>/notes.md` 的取舍说明必须与脚本全文在 UI 中并列展示**，由人核对"方案约束是否被满足"——这是本工具唯一能覆盖该风险的手段；② 运行**不保证幂等**（脚本可能改动运行目录，例如自建 `logs/`），重跑前要留意；③ 不要在任何文案里把 `succeeded` 说成"方案已正确实现"。

## 12. UI 规格

单窗口，三区 + 底栏 + 两个独立页（环境自检、设置）。PySide6 落点：`QMainWindow` + `QSplitter`（三栏）、`QPlainTextEdit`（脚本与日志）、`QTreeWidget`（shellcheck 按 SC 编号分组）、`QListWidget`（轮次时间线与历史）、`QTabWidget`（设置页、自检页）。**引擎跑在 `QThread` 里，通过 Qt 信号把 `RunEvent` 投递到主线程**；引擎自身不认识 Qt，因此可用纯 pytest 测。

- **左栏**：方案（选文件/拖入 + 文本预览 + 摘要）；模板库（列表 + 编辑器 + 占位符表单 + `trusted` 开关）；本次运行参数（运行根目录、阻断级别、轮次上限、两个超时）。全局项不在此处：**组件路径（opencode/Git Bash/shellcheck 的 exe 位置）只出现在"设置"页**，避免两处可改。
- **中栏**：本轮脚本全文（行号 + 与上一轮 diff 切换）；轮次时间线（每轮：阶段、结论、耗时）。
- **右栏**：shellcheck 报告（按 SC 编号分组，点行号跳到中栏对应行，显示 level/说明）；执行输出（stdout/stderr 分色、退出码、耗时、超时/取消标记）。
- **底栏**：开始 / 取消 / 继续修复 / 打开运行目录 / 历史列表。
- 事件流驱动更新：订阅 SSE，用 `message.part.updated` 渲染**增量**的助手文本与工具调用部件（`pending/running/completed/error` 四态分别有视觉区分），因此"生成中"是真流式而非空白等待；若某版本未发出增量事件，退化为阶段级进度提示（兜底，不阻塞验收）。

## 13. 错误处理与边界

| 情况 | 处理 |
| --- | --- |
| 依赖缺失/版本过低 | 自检页阻断式报错 + 指引；不进入运行 |
| server 起不来 / health 不通 | 展示 `server.log` 尾部与命令原文，`aborted_dependency` |
| 结构化输出失败（`StructuredOutputError`） | 计一次契约失败，回灌"返回的 JSON 不符合 schema" |
| 返回脚本为空 / 超 64KB / 缺锚点 | 契约失败，回灌缺失锚点清单 |
| shellcheck `2/3/4` | 依赖或调用错误，不进修复循环，直接报错 |
| 执行超时 / 用户取消 | 杀进程树，记录 `timedOut`/`cancelled`，进入下一轮或终止 |
| 3 轮未通过 | `needs_human`，展示全部证据，支持手工改后重校验/重执行 |
| 模板占位符未声明 | 渲染即报错，不启动运行 |
| 应用退出/崩溃 | 清理 opencode server 与执行进程树；运行目录保留可回放 |

## 14. 测试策略

**Linux 上可跑（CI 等价）**

- `orchestrator`：注入 fake `OpencodePort` 与 fake `ToolchainPort`，覆盖三条主路径（一次通过 / 第 2 轮修好 / 3 轮转人工）、取消、超时、契约失败、shellcheck 退出码 2/3/4、server 启动失败。
- `template_store`：占位符渲染、未声明占位符报错、`trusted`、CRLF 归一化。
- `run_store`：目录布局、每轮落盘、索引重建、回放。
- `shell_toolchain`：真实 bash + 项目内 `tools/shellcheck`（0.11.0 静态二进制），验证 `json1` 解析、退出码映射、`SHELLCHECK_OPTS` 清理、超时与进程树终止（POSIX 进程组 / Windows `taskkill`）。
- `ui`：三区渲染与事件驱动更新（pytest-qt，Plan 2）。

**端到端夹具**（两个，必须真实跑）

1. 语法错误方案：故意产出 `for f in $(ls); do ...` 未加引号等问题的脚本 → 验证 shellcheck 捕获 → 回灌 → 第 2 轮修好 → 执行成功。
2. 锚点破坏夹具：让 fake adapter 返回删掉锚点的脚本 → 验证 contract 失败路径与回灌内容准确。

**只能在 Windows 手测**

- Git Bash 探测与 `bash.exe --noprofile --norc` 执行；进程树取消（`taskkill /T /F`）。
- shellcheck 探测与 winget 安装指引；UTF-8 输出无乱码。
- opencode 原生安装下的 `serve` 启动、agent 发现（`opencode agent list`）、结构化输出实际可用。
- **权限确实生效**（整个安全模型的地基）：在受控运行目录里让 agent 尝试执行一条无害命令、尝试写一个文件，确认结果是**被拒绝**而不是弹出 `ask` 询问导致挂起；并确认用户全局配置里把 `bash` 设为 `allow` 也覆盖不了本 agent 的 `deny`。**规则合并这一半已在 Linux + 1.18.31 上实测通过**（见 §18 风险 6），Windows 侧复核实跑行为即可。
- PyInstaller 产物：one-folder 目录与单文件便携 exe 双击可用；PySide6 的 Qt 插件（`platforms/`、`styles/`）被正确收集，界面能起来。
- 中文路径与含空格路径（`C:\Users\张三\我的 方案.md`）。

## 15. 验收标准

1. Windows 干净机器：自检页正确列出 opencode/Git Bash/shellcheck 的版本与路径；缺任一项时给出可执行的安装指引。
2. 选一份方案 + 一套模板 → 3 秒内看到骨架预览 → 开始运行 → 每轮脚本、报告、输出都可见且与磁盘文件一致。
3. 语法错误注入夹具：第 2 轮自动修好，UI 明确显示"第 2 轮修复"。
4. `trusted` 模板下不弹确认直接执行；非 trusted 模板每次执行前必须确认。
5. 3 轮失败后进入 `needs_human`，可手工改脚本后只重跑 shellcheck + 执行。
6. 运行目录整体拷到另一台机器仍可离线回放历史。
7. 应用退出后无残留 `opencode`/`bash` 进程。

## 16. 与先前批准设计的差异（需确认）

1. **产出契约变更**：由"opencode 用 edit 工具把脚本写到 `script.sh`"改为"结构化输出返回脚本文本，后端自己写盘"。权限随之从 `edit: allow` 收紧到 `edit: deny`（`bash: deny` 不变）。理由：契约可校验、消除"没写出文件"的失败类别、opencode 连写盘权都不需要。
2. **传输确定为 serve + SDK**（原为"倾向 (b)，待调研定稿"）：结构化输出只有 server/SDK 提供。
3. **新增 `external_directory: deny`**：其默认 `ask` 会让运行静默挂住。
4. **shellcheck 调用参数固定**（`--norc -s bash -f json1`，清理 `SHELLCHECK_OPTS`），阻断级别默认 `info`（理由见 §11）。
5. **明确要求 Windows 原生 opencode**，不做 WSL 路径映射。
6. **技术栈整体修订为「全 Python」**（用户于 2026-09-17 裁定，见 §19）：Electron / TypeScript / React / vitest / electron-builder 全部替换为 PySide6 / httpx / pytest / PyInstaller；opencode 接入从官方 JS SDK 改为直打 HTTP/SSE。§5–§13 的架构（分层、端口、状态机、契约、权限模型）不受影响。

## 17. 建议实施分期

单一项目，但按可独立验证的里程碑推进，每期结束都有可运行产物：

1. **M1 工具链**：`shell-toolchain`（探测 + shellcheck + 执行 + 杀进程树）+ 设置页 + 环境自检页。此时可在本机 Linux 上真实跑通 shellcheck 与执行。
2. **M2 opencode 接入**：`opencode-adapter` + agent 定义生成 + 结构化输出 + 会话续跑；用手指夹具脚本替代 UI 验证生成与修复闭环。
3. **M3 编排与界面**：`orchestrator` 状态机 + `template-store` + `run-store` + 三区 UI + 历史回放。
4. **M4 打包与 Windows 验收**：PyInstaller 出 one-folder 与单文件便携 exe，执行 §14 的 Windows 手测清单与 §15 验收标准。

## 18. 未决风险

1. opencode 在 Windows 原生运行是官方不推荐路径（官方推荐 WSL）；若实测问题严重，应急路径（§7.1）或"改为要求 WSL"会浮上台面。
2. ~~SDK 事件流的增量粒度未知~~ —— **已由源码调研消除**：`message.part.updated` 提供增量部件，真流式可实现；§12 的降级仅为兜底。
3. 同一会话 3 轮往返的上下文膨胀可能触发 opencode 自身压缩，导致它"忘记"模板细节 —— 缓解：每轮回灌都重新附上模板骨架与锚点清单。
4. 长脚本走结构化输出存在截断风险：已用 64KB 上限 + schema 校验 + `StructuredOutputError` 三重兜底，仍失败则计契约失败并回灌。
5. 用户已安装的 opencode 版本未知：自检记录版本并在 < 1.1.1 时警告。
6. `permission: {"*": deny}` 这种**总键**写法在文档示例里出现过（与具体键并存），但源码级调研只列出了具体权限键（`read/edit/glob/grep/list/bash/task/external_directory/lsp/skill` + `todowrite/question/webfetch/websearch/doom_loop`）。因此实现时**不把安全模型只押在总键上**：逐键显式 `deny`。**该项已在 Linux 上用真实 1.18.31 实证**：把带 `bash: deny / edit: deny / read` 白名单的 agent 定义放进运行目录后，`GET /agent` 返回的合并规则里确实出现 `bash * -> deny`、`edit * -> deny`、`read * -> deny` 与 `read <白名单> -> allow`，**agent 的 deny 覆盖了全局的 `* -> allow`**。Windows 侧只需复核同一行为。
7. 原生 Windows 上的 opencode 全局配置目录文档未写明（只给 `~/.config/opencode/`）：本应用只用**项目级** `.opencode/agents/`，因此不依赖它；仅当将来要做全局安装时才需确认。
8. opencode 自身在 Windows 用哪个 shell（源码里 `pwsh 优先` 与 `cmd.exe 默认` 两条路径并存）与本应用无关 —— 因为 agent 的 `bash` 权限被拒绝，执行一律由后端直接调用 Git Bash。这条只有在启用 §7.1 应急路径时才需要重新评估。
9. **没有官方 Python SDK**：端点契约直接依赖 opencode server 的 HTTP 接口，端点漂移是真实风险（已观察到 2.x 把 API 整体搬到 `/api/*` 且规范里没有结构化输出）。缓解：自检记录 server 版本；`opencode_adapter` 是唯一接触端点的地方，漂移只需改一个模块。**1.18.31 的契约已用真实二进制核对**（请求字段 `format`、结果字段 `AssistantMessage.structured`、`StructuredOutputError`、`POST /session/{sessionID}/permissions/{permissionID}`、`GET /global/health` → `{"healthy":true,"version":"1.18.31"}`），因此 `format` vs `outputFormat` 这处文档不一致**已不再是风险**——顺带发现结果字段名与 JS SDK 文档不同，实现里两个都读。
10. PySide6 + PyInstaller 在 Windows 上的产物体积约 150–250MB（Qt 运行时），且首次打包可能需要补 hook —— M4 的 Windows 手测包含"干净机器双击可用"。

## 19. 技术栈修订记录（2026-09-17）

用户在实现阶段裁定：**全 Python（PySide6 桌面界面 + Python 后端）**，取代原先的 Electron + TypeScript + React。

**为什么改**：用户明确要以 Python 开发这个 agent。原技术栈是我在"交付形态=独立桌面应用"这个答复之上自行选定的，用户未反对但也没有选定语言；一旦明确，越早改越便宜。

**受影响**：语言与运行时（Node → Python 3.10+）、界面（Electron/React → PySide6）、opencode 接入（官方 JS SDK → `httpx` 直打 HTTP/SSE）、测试（vitest → pytest / pytest-qt）、打包（electron-builder → PyInstaller）。

**不受影响**（这是架构与语言解耦的收益）：§5 的分层与端口、§6 状态机、§7 的 agent 定义/产出契约/失败回灌/自动拒绝权限、§8 模板系统、§9 环境自检、§10 磁盘布局、§11 执行与校验、§13 错误处理、§15 验收标准。

**已验证的可行性**（本机 Arch + Python 3.14.7）：PySide6 6.11.2（cp310-abi3 轮子）、pytest 9.1.1、pytest-qt 4.5.0、PyInstaller 6.22.3 均可解析安装；`httpx` 系统已装。
