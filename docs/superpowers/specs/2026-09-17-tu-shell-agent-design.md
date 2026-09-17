# tu-shell-agent 设计规格

- 状态：待用户审查
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
- JS/TS SDK：`@opencode-ai/sdk`，`createOpencode()` 起 server+client，或 `createOpencodeClient({baseUrl})` 接已有 server；`session.create/prompt/abort/messages` 等。[SDK 文档](https://opencode.ai/docs/sdk/)
- **结构化输出**：`session.prompt` 的 body 可带 `format: {type:'json_schema', schema, retryCount?}`，模型通过内部 `StructuredOutput` 工具返回**经 schema 校验的 JSON**（结果在 `info.structured_output`）；失败抛出 `StructuredOutputError`（含 `retries`）。这是只有 server/SDK 才有的能力，CLI 没有对应 flag。[SDK 文档](https://opencode.ai/docs/sdk/#structured-output)
- Agent 定义：markdown + frontmatter 放在全局 `~/.config/opencode/agents/` 或项目级 `.opencode/agents/`，**文件名即 agent 名**；也可写在 `opencode.json` 的 `agent` 键下。frontmatter 支持 `description`（必填）、`mode: primary|subagent|all`、`model`、`temperature`、`permission`、`steps`、`disable`、`hidden` 等。[Agents 文档](https://opencode.ai/docs/agents/)
- 非交互生成 agent：`opencode agent create --path <dir> --description <d> --mode <m> --permissions <list> [--model]`，四个参数齐全即不进入交互；`--permissions` 可取值 `bash,read,edit,glob,grep,webfetch,task,todowrite,websearch,lsp,skill`，**未列出的权限一律拒绝**。[CLI 文档](https://opencode.ai/docs/cli/#create)
- 权限模型：取值 `allow | ask | deny`；支持对象式细粒度规则（按输入匹配，`*`/`?` 通配，**最后一条匹配的规则生效**）；键包括 `read, edit, glob, grep, bash, task, skill, lsp, question, webfetch, websearch, external_directory, doom_loop`；默认大多为 `allow`，`doom_loop` 与 `external_directory` 默认 `ask`，`.env` 默认拒绝。**agent 的权限与全局配置合并，且 agent 规则优先**。[Permissions 文档](https://opencode.ai/docs/permissions/)
- 事件流是 **SSE**：`GET /event`（首事件 `server.connected`）与 `GET /global/event`；信封为 `{type, properties}`，事件类型里 `message.part.updated` 提供**增量**的助手文本与工具调用部件（`ToolState = pending | running | completed | error`）。→ §12 的真流式有据可依。
- 权限询问**可以编程回答**：`POST /session/:id/permissions/:permissionID`，body `{response: 'once' | 'always' | 'reject'}`；配合 `permission.updated` 事件可在 UI 侧接管。→ 见 §7.2 的自动拒绝安全网。
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
| 外壳 | Electron | 用户机器上 DSH Desktop 就是 Electron 2.0.2，Windows 打包链路已熟悉；后端就是 Node，spawn opencode / Git Bash / shellcheck 最直接 |
| 语言 | TypeScript | 事件与状态机需要类型约束 |
| 渲染层 | React + Vite | 三区布局 + 事件流驱动，组件化收益明确 |
| 打包 | electron-builder，NSIS 安装包 + 便携 exe | 覆盖"双击即用" |
| 测试 | vitest | 与 Vite 同栈 |
| opencode 接入 | `@opencode-ai/sdk` + 自管 `opencode serve` | 见 §7.1 |
| 模板引擎 | 自实现简单替换（`{{name}}`） | 见 §8，YAGNI |

## 5. 架构与组件

单向依赖：`renderer → (IPC) → main → orchestrator → {opencode-adapter, shell-toolchain, stores}`。orchestrator 与 stores **不 import 任何 Electron 或 child_process**，因此可在 Linux 上纯单测。

| 组件 | 职责 | 依赖 |
| --- | --- | --- |
| `main` | 窗口、IPC 路由、生命周期、退出时清理子进程 | Electron |
| `orchestrator` | 运行状态机（§6）。输入=方案+模板+配置，输出=事件流；唯一"知道流程"的地方 | 下面几个的接口 |
| `opencode-adapter` | 唯一与 opencode 通信处。对外：`startRun({runDir, agentName, model?})` → `{sessionId}`；`generate({sessionId, prompt, schema})` → `{structured, events}`；`abort()`；`dispose()` | `@opencode-ai/sdk`、child_process |
| `shell-toolchain` | `detect()` 环境自检；`shellcheck(path, opts)` → 归一化报告；`execute(path, opts)` → 流式输出 + 退出码；`killTree(pid)` | child_process |
| `template-store` | 模板 CRUD、占位符元数据、`trusted` 标记、持久化 | fs |
| `run-store` | 运行目录布局、每轮落盘、历史索引与回放 | fs |
| `renderer` | 只发命令、只渲染事件；不含业务判断 | IPC |

`opencode-adapter` 的接口刻意与传输方式解耦：v1 用 SDK 实现，若在 Windows 实测 server 起不来，可换成 CLI 实现（§7.1 应急路径）而不动 orchestrator。

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
- 由本应用 spawn（而非 SDK 的 `createOpencode()` 自动拉起），以便把 server 的 stdout/stderr 收进 `server.log` 并在自检页展示。
- 客户端：`createOpencodeClient({baseUrl})` + Basic 认证头。
- 运行结束（含取消/崩溃）必须 `dispose()` 并杀掉 server 进程树。

**为什么不是"CLI 优先"**：结构化输出（§7.3）只有 server/SDK 有，而它正是产出契约可靠性的来源；SDK 也是官方给出的集成面。

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
- `external_directory: deny` 是必需的：它默认为 `ask`。而"不问"这件事不能只靠配置正确 —— 适配器必须订阅事件流，**对任何 `permission.updated` 一律回 `reject`**（`POST /session/:id/permissions/:permissionID`，body `{response:'reject'}`），并在 UI 上标注"已自动拒绝"。这样即使规则写漏，运行也只会被拒绝，不会静默挂住。
- 绝对路径（`<runDir>`）在生成时填入，所以 `read` 被锁死在本次运行目录内。
- **不写 `model` 字段**：按官方语义，未指定时 primary agent 使用用户全局配置的模型，这样应用不硬编码也不覆盖用户的供应商设置；设置页可选地固定 `provider/model`，取值来自 `opencode models` 校验。
- 运行前用 `opencode agent list`（cwd = runDir）**验证该 agent 已被发现**；未发现则判 `aborted_dependency` 并展示目录内容与命令输出，不做静默回退。

### 7.3 产出契约（结构化输出）

`session.prompt` 带 JSON schema：

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
- 阻断级别默认 `warning`（error 与 warning 触发回灌修复），`info`/`style` 只展示；可配。

**执行**

```
<git>\bin\bash.exe --noprofile --norc <runDir>\script.sh
```

- 工作目录默认 = 运行目录，确认页可改。
- stdout/stderr 分别流式回传并落盘；超时默认 120s（可配）；取消时用 `taskkill /PID <pid> /T /F` 杀整棵进程树。
- 输出按 UTF-8 解码；遇非法字节用有损解码并在 UI 标注（避免 Windows 代码页导致的乱码假象）。
- 每次执行前展示脚本全文确认；模板标记 `trusted` 则跳过（§8）。确认页对危险模式（`rm -rf`、`mkfs`、`dd`、`> /dev/sd*`、`chmod -R 777 /`、`curl|bash`）高亮提示。
- 换行：写入时强制 LF（生成结果里的 CRLF 归一化），并在执行前校验文件无 `\r`。

## 12. UI 规格

单窗口，三区 + 底栏 + 两个独立页（环境自检、设置）。

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

- `orchestrator`：注入 fake `opencode-adapter` 与 fake `shell-toolchain`，覆盖三条主路径（一次通过 / 第 2 轮修好 / 3 轮转人工）、取消、超时、契约失败、shellcheck 退出码 2/3/4。
- `template-store`：占位符渲染、未声明占位符报错、`trusted`、CRLF 归一化。
- `run-store`：目录布局、每轮落盘、索引重建、回放。
- `shell-toolchain`：本机真实 bash + 已安装的 shellcheck（Arch 上 `pacman -S shellcheck`），验证 `json1` 解析、退出码映射、`SHELLCHECK_OPTS` 清理、超时与进程树终止。
- `renderer`：三区渲染与事件驱动更新（组件级）。

**端到端夹具**（两个，必须真实跑）

1. 语法错误方案：故意产出 `for f in $(ls); do ...` 未加引号等问题的脚本 → 验证 shellcheck 捕获 → 回灌 → 第 2 轮修好 → 执行成功。
2. 锚点破坏夹具：让 fake adapter 返回删掉锚点的脚本 → 验证 contract 失败路径与回灌内容准确。

**只能在 Windows 手测**

- Git Bash 探测与 `bash.exe --noprofile --norc` 执行；进程树取消（`taskkill /T /F`）。
- shellcheck 探测与 winget 安装指引；UTF-8 输出无乱码。
- opencode 原生安装下的 `serve` 启动、agent 发现（`opencode agent list`）、结构化输出实际可用。
- **权限确实生效**（整个安全模型的地基，必须显式验证）：在受控运行目录里让 agent 尝试执行一条无害命令、尝试写一个文件，确认结果是**被拒绝**而不是弹出 `ask` 询问导致挂起；并确认用户全局配置里把 `bash` 设为 `allow` 也覆盖不了本 agent 的 `deny`。
- `electron-builder` 产物：NSIS 安装包 + 便携 exe 双击可用。
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
4. **shellcheck 调用参数固定**（`--norc -s bash -f json1`，清理 `SHELLCHECK_OPTS`），阻断级别默认 `warning`。
5. **明确要求 Windows 原生 opencode**，不做 WSL 路径映射。

## 17. 建议实施分期

单一项目，但按可独立验证的里程碑推进，每期结束都有可运行产物：

1. **M1 工具链**：`shell-toolchain`（探测 + shellcheck + 执行 + 杀进程树）+ 设置页 + 环境自检页。此时可在本机 Linux 上真实跑通 shellcheck 与执行。
2. **M2 opencode 接入**：`opencode-adapter` + agent 定义生成 + 结构化输出 + 会话续跑；用手指夹具脚本替代 UI 验证生成与修复闭环。
3. **M3 编排与界面**：`orchestrator` 状态机 + `template-store` + `run-store` + 三区 UI + 历史回放。
4. **M4 打包与 Windows 验收**：electron-builder 出 NSIS/便携 exe，执行 §14 的 Windows 手测清单与 §15 验收标准。

## 18. 未决风险

1. opencode 在 Windows 原生运行是官方不推荐路径（官方推荐 WSL）；若实测问题严重，应急路径（§7.1）或"改为要求 WSL"会浮上台面。
2. ~~SDK 事件流的增量粒度未知~~ —— **已由源码调研消除**：`message.part.updated` 提供增量部件，真流式可实现；§12 的降级仅为兜底。
3. 同一会话 3 轮往返的上下文膨胀可能触发 opencode 自身压缩，导致它"忘记"模板细节 —— 缓解：每轮回灌都重新附上模板骨架与锚点清单。
4. 长脚本走结构化输出存在截断风险：已用 64KB 上限 + schema 校验 + `StructuredOutputError` 三重兜底，仍失败则计契约失败并回灌。
5. 用户已安装的 opencode 版本未知：自检记录版本并在 < 1.1.1 时警告。
6. `permission: {"*": deny}` 这种**总键**写法在文档示例里出现过（与具体键并存），但源码级调研只列出了具体权限键（`read/edit/glob/grep/list/bash/task/external_directory/lsp/skill` + `todowrite/question/webfetch/websearch/doom_loop`）。因此实现时**不把安全模型只押在总键上**：要求逐键显式 `deny`，并在 M2 的 Windows 手测里实测"全局设 `bash: allow` 也覆盖不了 agent 的 `deny`"（§14）。
7. 原生 Windows 上的 opencode 全局配置目录文档未写明（只给 `~/.config/opencode/`）：本应用只用**项目级** `.opencode/agents/`，因此不依赖它；仅当将来要做全局安装时才需确认。
8. opencode 自身在 Windows 用哪个 shell（源码里 `pwsh 优先` 与 `cmd.exe 默认` 两条路径并存）与本应用无关 —— 因为 agent 的 `bash` 权限被拒绝，执行一律由后端直接调用 Git Bash。这条只有在启用 §7.1 应急路径时才需要重新评估。
