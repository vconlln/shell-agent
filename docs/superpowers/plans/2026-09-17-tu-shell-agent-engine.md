# tu-shell-agent 核心引擎实现计划（Plan 1 / 2）

> **面向 AI 代理的工作者：** 必需子技能：使用 superpowers:subagent-driven-development（推荐）或 superpowers:executing-plans 逐任务实现此计划。步骤使用复选框（`- [ ]`）语法来跟踪进度。

**目标：** 交付一个无界面的核心引擎：给定"方案文档 + shell 模板"，调用本机 opencode 生成脚本，用 shellcheck 静态校验、用 bash（Git Bash 路径可配）执行，失败时把结构化证据回灌同一 opencode 会话自修，最多 3 轮。

**架构：** 引擎与 Electron 完全解耦：`orchestrator`（纯状态机，依赖注入）位于中间，两侧是 `opencode-adapter`（唯一与 opencode 通信处，走 `opencode serve` + SDK）与 `shell-toolchain`（探测 / shellcheck / 执行），加两个存储（`template-store`、`run-store`）。本计划结束时可用 `tsx src/engine/cli.ts` 在命令行跑完整个流程并打印时间线。

**技术栈：** TypeScript（ESM）、Node ≥ 22、`@opencode-ai/sdk`、vitest、tsx；开发与测试机为 Arch Linux（真实 bash + 真实 shellcheck + 真实 opencode），Windows 专属分支在手测清单里验证。

**规格：** `docs/superpowers/specs/2026-09-17-tu-shell-agent-design.md`（commit `92b85c9` + `ee7c3ca`）

---

## 前置准备（人工，一次性）

Arch 上装齐两个外部依赖（需要 sudo，会触发审批）：

```bash
sudo pacman -S --needed shellcheck opencode
shellcheck --version && opencode --version
```

预期：shellcheck 0.11.x、opencode 1.18.x。opencode 需要先登录至少一个供应商，否则第 12 个任务的真实冒烟无法通过：

```bash
opencode auth list        # 若无条目：opencode auth login
```

> 本机没有 Windows 的 Git Bash，因此引擎把 bash 解释器路径做成配置项：Linux 上默认 `/usr/bin/bash`，Windows 上默认探测 Git Bash。这正是"可移植"的落点，不是临时妥协。

## 文件结构

每个文件一个职责，先锁定边界再拆任务。

```
tu-shell-agent/
  package.json                          ESM、scripts、依赖
  tsconfig.json                         strict、NodeNext
  vitest.config.ts
  src/engine/
    types.ts                            全部共享类型与端口接口（唯一类型来源）
    ports.ts                            OpencodePort / ToolchainPort / ConfirmPort / RunStorePort
    shell-toolchain/
      detect.ts                         环境探测（opencode/bash/shellcheck），平台分支集中在此
      shellcheck.ts                     调 shellcheck 并归一化报告与退出码
      execute.ts                        spawn bash、流式输出、超时、杀进程树
      index.ts                          组装成 ToolchainPort
    opencode-adapter/
      agent-file.ts                     生成项目级 agent 定义（逐键 deny）
      server.ts                         起停 opencode serve（端口、密码、日志、health 轮询）
      events.ts                         SSE 订阅（原生 fetch，自解析）
      prompt.ts                         输出 schema + 每轮 user message 组装
      index.ts                          组装成 OpencodePort（含自动 reject 权限）
    template-store/
      render.ts                         {{name}} 渲染、未声明即报错、LF 归一化
      builtins.ts                       3 套内置模板
      store.ts                          模板 CRUD + index.json
    run-store/
      layout.ts                         运行目录布局与路径推导
      store.ts                          每轮落盘、meta.json、索引
    orchestrator/
      contract.ts                       锚点/空/超长/CRLF 契约校验
      loop.ts                           主状态机（唯一"知道流程"的地方）
    cli.ts                              开发驱动：跑完整流程并打印时间线
  test/fixtures/
    plan-simple.md                      简单方案（真实冒烟用）
    template-single.tpl.sh              测试用模板
```

**分层规则（实现时不得违反）：**
- `orchestrator/` 与 `*-store/` **不 import** `child_process`、`fs/promises` 之外的系统能力，更不 import Electron；外部世界一律通过 `ports.ts` 注入。
- 只有 `shell-toolchain/` 与 `opencode-adapter/` 允许 spawn 进程。
- `types.ts` 是类型的唯一来源；任何模块不得私自定义与它重复的结构。

---

### 任务 1：项目脚手架与共享类型

**文件：**
- 创建：`package.json`、`tsconfig.json`、`vitest.config.ts`、`src/engine/types.ts`、`src/engine/ports.ts`
- 测试：`src/engine/types.test.ts`

- [ ] **步骤 1：写失败的测试**

```ts
// src/engine/types.test.ts
import { describe, expect, it } from 'vitest'
import { SEVERITY_RANK, blocksRun } from './types.js'

describe('严重级别', () => {
  it('error 比 warning 更严重', () => {
    expect(SEVERITY_RANK.error).toBeGreaterThan(SEVERITY_RANK.warning)
  })

  it('阻断级别为 warning 时，error 与 warning 都阻断，info 不阻断', () => {
    expect(blocksRun('error', 'warning')).toBe(true)
    expect(blocksRun('warning', 'warning')).toBe(true)
    expect(blocksRun('info', 'warning')).toBe(false)
    expect(blocksRun('style', 'warning')).toBe(false)
  })
})
```

- [ ] **步骤 2：运行测试验证失败**

运行：`npx vitest run src/engine/types.test.ts`
预期：FAIL，`Failed to resolve import "./types.js"`

- [ ] **步骤 3：写最小实现**

```jsonc
// package.json
{
  "name": "tu-shell-agent",
  "version": "0.1.0",
  "private": true,
  "type": "module",
  "scripts": {
    "test": "vitest run",
    "test:watch": "vitest",
    "typecheck": "tsc --noEmit",
    "cli": "tsx src/engine/cli.ts"
  },
  "dependencies": {
    "@opencode-ai/sdk": "^1.18.0"
  },
  "devDependencies": {
    "@types/node": "^22.0.0",
    "tsx": "^4.19.0",
    "typescript": "^5.6.0",
    "vitest": "^2.1.0"
  }
}
```

```jsonc
// tsconfig.json
{
  "compilerOptions": {
    "target": "ES2023",
    "module": "NodeNext",
    "moduleResolution": "NodeNext",
    "strict": true,
    "noUncheckedIndexedAccess": true,
    "exactOptionalPropertyTypes": true,
    "skipLibCheck": true,
    "noEmit": true,
    "types": ["node"]
  },
  "include": ["src", "test"]
}
```

```ts
// vitest.config.ts
import { defineConfig } from 'vitest/config'
export default defineConfig({ test: { include: ['src/**/*.test.ts'], testTimeout: 30_000 } })
```

```ts
// src/engine/types.ts
export type Severity = 'error' | 'warning' | 'info' | 'style'

export const SEVERITY_RANK: Record<Severity, number> = { error: 3, warning: 2, info: 1, style: 0 }

/** 某条发现是否应当阻断本次运行（级别 ≥ 配置的阻断级别）。 */
export function blocksRun(level: Severity, blockingLevel: Severity): boolean {
  return SEVERITY_RANK[level] >= SEVERITY_RANK[blockingLevel]
}

export type Stage = 'contract' | 'shellcheck' | 'execute'
export type RunOutcome = 'succeeded' | 'needs_human' | 'cancelled' | 'aborted_dependency'

export interface ShellcheckFinding {
  code: string
  line: number
  column: number
  level: Severity
  message: string
}

export interface ExecuteResult {
  exitCode: number | null
  signal: string | null
  timedOut: boolean
  cancelled: boolean
  durationMs: number
  stdout: string
  stderr: string
}

export type ContractFailure = 'empty' | 'too_large' | 'has_crlf' | 'missing_anchor'

export interface ContractResult {
  ok: boolean
  reason?: ContractFailure
  missingAnchors: string[]
  bytes: number
}

export interface FailureEvidence {
  round: number
  stage: Stage
  /** message 用于"这一轮根本没产出脚本"（结构化输出失败/超时），此时 reason 记 empty。 */
  contract?: { reason: ContractFailure; missingAnchors: string[]; message?: string }
  shellcheck?: ShellcheckFinding[]
  execute?: { exitCode: number | null; timedOut: boolean; stdoutTail: string; stderrTail: string; durationMs: number }
}

export interface GeneratedScript {
  script: string
  notes: string
  assumptions: string[]
}

export interface DetectedTool {
  path: string
  version: string
}

export interface DetectionReport {
  opencode?: DetectedTool
  bash?: DetectedTool
  shellcheck?: DetectedTool
  problems: string[]
}

export interface RunConfig {
  runRoot: string
  maxRounds: number
  generateTimeoutMs: number
  executeTimeoutMs: number
  blockingLevel: Severity
  bashPath?: string
  shellcheckPath?: string
  opencodePath?: string
  model?: string
}

export type RunEvent =
  | { type: 'phase'; phase: 'precheck' | 'generating' | 'checking' | 'confirming' | 'executing' | 'repairing' | 'settled'; round: number }
  | { type: 'assistant_delta'; round: number; text: string }
  | { type: 'script'; round: number; script: string }
  | { type: 'shellcheck'; round: number; findings: ShellcheckFinding[] }
  | { type: 'execute'; round: number; result: ExecuteResult }
  | { type: 'permission_rejected'; sessionId: string }
  | { type: 'note'; message: string }
```

```ts
// src/engine/ports.ts
import type {
  DetectionReport, ExecuteResult, GeneratedScript, RunConfig, ShellcheckFinding,
} from './types.js'

export interface ToolchainPort {
  detect(): Promise<DetectionReport>
  shellcheck(scriptPath: string): Promise<{ findings: ShellcheckFinding[]; exitCode: number; raw: string }>
  execute(
    scriptPath: string,
    opts: { cwd: string; timeoutMs: number; signal?: AbortSignal },
  ): Promise<ExecuteResult>
}

export interface OpencodePort {
  start(runDir: string, opts: { agentName: string; model?: string }): Promise<{ sessionId: string }>
  generate(
    sessionId: string,
    userMessage: string,
    opts: { schema: object; timeoutMs: number; onDelta?: (text: string) => void; signal?: AbortSignal },
  ): Promise<GeneratedScript>
  abort(sessionId: string): Promise<void>
  dispose(): Promise<void>
}

export interface ConfirmPort {
  /** 返回 false 表示用户拒绝执行本次脚本。 */
  confirm(input: { round: number; scriptPath: string; script: string; trusted: boolean }): Promise<boolean>
}

export interface RunStorePort {
  runDir: string
  writeScript(round: number, script: string): Promise<string>
  writeAttempt(round: number, files: Record<string, string>): Promise<void>
  writeMeta(patch: Record<string, unknown>): Promise<void>
}

export interface LoopPorts {
  opencode: OpencodePort
  toolchain: ToolchainPort
  confirm: ConfirmPort
  store: RunStorePort
  emit: (event: import('./types.js').RunEvent) => void
  now?: () => number
}
```

- [ ] **步骤 4：运行测试验证通过**

运行：`npx vitest run src/engine/types.test.ts && npx tsc --noEmit`
预期：PASS，且 tsc 无错误

- [ ] **步骤 5：Commit**

```bash
git add package.json tsconfig.json vitest.config.ts src/engine/types.ts src/engine/ports.ts src/engine/types.test.ts
git commit -m "feat(engine): 脚手架与共享类型

引入严重级别判定、运行事件、端口接口；orchestrator 依赖端口而非具体实现。"
```

> 注意：`npm install` 需在步骤 4 之前执行一次（`npm install`，本机 pnpm 会段错误，用 npm）。

---

### 任务 2：模板渲染

**文件：**
- 创建：`src/engine/template-store/render.ts`
- 测试：`src/engine/template-store/render.test.ts`

- [ ] **步骤 1：写失败的测试**

```ts
// src/engine/template-store/render.test.ts
import { describe, expect, it } from 'vitest'
import { renderTemplate } from './render.js'

const body = '#!/usr/bin/env bash\nset -euo pipefail\n# @@TU:BODY@@\necho {{greeting:hello}} {{name}}\n'

describe('renderTemplate', () => {
  it('替换已声明的占位符并使用默认值', () => {
    const out = renderTemplate(body, [{ name: 'name', default: 'world' }, { name: 'greeting' }], {})
    expect(out).toContain('echo hello world')
  })

  it('调用方提供的值优先于默认值', () => {
    const out = renderTemplate(body, [{ name: 'name' }, { name: 'greeting' }], { name: '张三', greeting: '你好' })
    expect(out).toContain('echo 你好 张三')
  })

  it('遇到未声明的占位符直接报错，不静默留空', () => {
    expect(() => renderTemplate(body, [{ name: 'greeting' }], {}))
      .toThrowError(/未声明的占位符.*name/)
  })

  it('把 CRLF 归一化为 LF', () => {
    const crlf = 'line1\r\nline2\r\n'
    expect(renderTemplate(crlf, [], {})).toBe('line1\nline2\n')
  })
})
```

- [ ] **步骤 2：运行测试验证失败**

运行：`npx vitest run src/engine/template-store/render.test.ts`
预期：FAIL，`Failed to resolve import "./render.js"`

- [ ] **步骤 3：写最小实现**

```ts
// src/engine/template-store/render.ts
export interface PlaceholderSpec {
  name: string
  default?: string
  description?: string
}

const PLACEHOLDER = /\{\{([A-Za-z_][A-Za-z0-9_]*)(?::([^}]*))?\}\}/g

export function toLf(text: string): string {
  return text.replace(/\r\n?/g, '\n')
}

/**
 * 只做字符串替换，不引入模板引擎（规格 §8）。
 * 未在元数据里声明的占位符 → 抛错，避免生成一个缺参数的脚本。
 */
export function renderTemplate(
  body: string,
  declared: PlaceholderSpec[],
  values: Record<string, string>,
): string {
  const byName = new Map(declared.map((p) => [p.name, p]))
  const undeclared = new Set<string>()

  const rendered = toLf(body).replace(PLACEHOLDER, (_all, name: string, inlineDefault?: string) => {
    const spec = byName.get(name)
    if (spec === undefined) {
      undeclared.add(name)
      return ''
    }
    const value = values[name] ?? spec.default ?? inlineDefault ?? ''
    return value
  })

  if (undeclared.size > 0) {
    throw new Error(`模板含未声明的占位符：${[...undeclared].sort().join(', ')}`)
  }
  return rendered
}

/** 取出模板里 `<name>` 中声明的占位符名（供 UI 生成表单）。 */
export function declaredNames(body: string): string[] {
  const names = new Set<string>()
  for (const m of toLf(body).matchAll(PLACEHOLDER)) names.add(m[1]!)
  return [...names].sort()
}
```

- [ ] **步骤 4：运行测试验证通过**

运行：`npx vitest run src/engine/template-store/render.test.ts`
预期：PASS（4 个用例）

- [ ] **步骤 5：Commit**

```bash
git add src/engine/template-store/render.ts src/engine/template-store/render.test.ts
git commit -m "feat(template): 占位符渲染与 LF 归一化

未声明占位符直接报错；CRLF 在入口处归一化，避免 Git Bash 报 \\r 错。"
```

---

### 任务 3：内置模板与模板库

**文件：**
- 创建：`src/engine/template-store/builtins.ts`、`src/engine/template-store/store.ts`
- 测试：`src/engine/template-store/store.test.ts`

- [ ] **步骤 1：写失败的测试**

```ts
// src/engine/template-store/store.test.ts
import { mkdtemp, readFile } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { beforeEach, describe, expect, it } from 'vitest'
import { BUILTIN_TEMPLATES } from './builtins.js'
import { TemplateStore } from './store.js'

let dir: string
beforeEach(async () => { dir = await mkdtemp(join(tmpdir(), 'tu-tpl-')) })

describe('TemplateStore', () => {
  it('首次打开写入 3 套内置模板', async () => {
    const store = new TemplateStore(dir)
    const list = await store.list()
    expect(list.map((t) => t.id).sort()).toEqual(['args-batch', 'logged-errors', 'single'])
    expect(BUILTIN_TEMPLATES).toHaveLength(3)
  })

  it('每套内置模板都含锚点，且渲染后锚点仍在', async () => {
    const store = new TemplateStore(dir)
    for (const meta of await store.list()) {
      const body = await store.read(meta.id)
      expect(body).toMatch(/# @@TU:[A-Z_]+@@/)
    }
  })

  it('保存自定义模板后可读回，并更新 index.json', async () => {
    const store = new TemplateStore(dir)
    await store.save({ id: 'mine', name: '我的模板', description: '', trusted: false, placeholders: [], body: 'echo hi # @@TU:BODY@@\n' })
    expect(await store.read('mine')).toContain('echo hi')
    const index = JSON.parse(await readFile(join(dir, 'index.json'), 'utf8'))
    expect(index.templates.map((t: { id: string }) => t.id)).toContain('mine')
  })

  it('删除模板会同时删除正文文件', async () => {
    const store = new TemplateStore(dir)
    await store.save({ id: 'gone', name: 'g', description: '', trusted: false, placeholders: [], body: '# @@TU:BODY@@\n' })
    await store.remove('gone')
    await expect(store.read('gone')).rejects.toThrow()
  })

  it('id 含非法字符时拒绝保存', async () => {
    const store = new TemplateStore(dir)
    await expect(store.save({ id: '../escape', name: 'x', description: '', trusted: false, placeholders: [], body: '' }))
      .rejects.toThrowError(/非法模板 id/)
  })
})
```

- [ ] **步骤 2：运行测试验证失败**

运行：`npx vitest run src/engine/template-store/store.test.ts`
预期：FAIL，`Failed to resolve import "./builtins.js"`

- [ ] **步骤 3：写最小实现**

三套内置模板的正文（规格 §8）。锚点用 `# @@TU:NAME@@`，每套至少一个 `BODY`：

```ts
// src/engine/template-store/builtins.ts
export interface BuiltinTemplate {
  id: string
  name: string
  description: string
  body: string
}

const SINGLE = `#!/usr/bin/env bash
set -euo pipefail

# @@TU:BODY@@

main() {
  :
}

main "$@"
`

const ARGS_BATCH = `#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'USAGE'
用法: {{script_name:task.sh}} [-n] [-d 目录]
USAGE
}

DIR="{{work_dir:.}}"
DRY_RUN=0
while getopts ":nd:h" opt; do
  case "$opt" in
    n) DRY_RUN=1 ;;
    d) DIR="$OPTARG" ;;
    h) usage; exit 0 ;;
    *) usage >&2; exit 2 ;;
  esac
done
shift $((OPTIND - 1))

if [[ ! -d "$DIR" ]]; then
  echo "目录不存在: $DIR" >&2
  exit 1
fi

# @@TU:BODY@@
`

const LOGGED_ERRORS = `#!/usr/bin/env bash
set -euo pipefail

LOG_PREFIX="{{log_prefix:task}}"
WORK_DIR=""
cleanup() {
  [[ -n "$WORK_DIR" && -d "$WORK_DIR" ]] && rm -rf "$WORK_DIR"
}
trap cleanup EXIT
trap 'echo "[$LOG_PREFIX] 第 $LINENO 行失败" >&2' ERR

log() { printf '[%s] %s\\n' "$LOG_PREFIX" "$*"; }
die() { printf '[%s] 错误: %s\\n' "$LOG_PREFIX" "$*" >&2; exit 1; }

WORK_DIR="$(mktemp -d)"
log "临时目录 $WORK_DIR"

# @@TU:BODY@@

log "完成"
`

export const BUILTIN_TEMPLATES: BuiltinTemplate[] = [
  { id: 'single', name: '单命令执行', description: '最小骨架：shebang + set -euo pipefail + main()', body: SINGLE },
  { id: 'args-batch', name: '参数解析批处理', description: 'getopts 解析、目录校验、usage', body: ARGS_BATCH },
  { id: 'logged-errors', name: '带日志与错误处理', description: 'log/die、trap ERR、mktemp 临时目录与退出清理', body: LOGGED_ERRORS },
]
```

```ts
// src/engine/template-store/store.ts
import { mkdir, readFile, readdir, rm, writeFile } from 'node:fs/promises'
import { join } from 'node:path'
import { BUILTIN_TEMPLATES } from './builtins.js'
import { declaredNames, type PlaceholderSpec } from './render.js'

export interface TemplateMeta {
  id: string
  name: string
  description: string
  trusted: boolean
  placeholders: PlaceholderSpec[]
  updatedAt?: string
}

export interface TemplateInput extends Omit<TemplateMeta, 'updatedAt'> {
  body: string
}

const ID_OK = /^[a-z0-9][a-z0-9-]*$/

export class TemplateStore {
  constructor(private readonly dir: string) {}

  private get indexPath(): string { return join(this.dir, 'index.json') }
  private bodyPath(id: string): string { return join(this.dir, `${id}.tpl.sh`) }

  private async readIndex(): Promise<{ templates: TemplateMeta[] }> {
    try {
      return JSON.parse(await readFile(this.indexPath, 'utf8')) as { templates: TemplateMeta[] }
    } catch {
      return { templates: [] }
    }
  }

  private async writeIndex(templates: TemplateMeta[]): Promise<void> {
    await writeFile(this.indexPath, `${JSON.stringify({ templates }, null, 2)}\n`, 'utf8')
  }

  /** 首次调用把内置模板写入目录；之后目录归用户所有。 */
  async list(): Promise<TemplateMeta[]> {
    await mkdir(this.dir, { recursive: true })
    let index = await this.readIndex()
    if (index.templates.length === 0) {
      for (const b of BUILTIN_TEMPLATES) {
        await writeFile(this.bodyPath(b.id), b.body, 'utf8')
        index.templates.push({
          id: b.id, name: b.name, description: b.description, trusted: false,
          placeholders: declaredNames(b.body).map((name) => ({ name })),
          updatedAt: new Date().toISOString(),
        })
      }
      await this.writeIndex(index.templates)
    }
    // 目录里出现 index 未登记的文件（用户手放）→ 登记之，保持索引可重建
    for (const file of await readdir(this.dir)) {
      if (!file.endsWith('.tpl.sh')) continue
      const id = file.slice(0, -'.tpl.sh'.length)
      if (index.templates.some((t) => t.id === id)) continue
      index.templates.push({ id, name: id, description: '', trusted: false, placeholders: [] })
      await this.writeIndex(index.templates)
    }
    return index.templates
  }

  async read(id: string): Promise<string> {
    return readFile(this.bodyPath(id), 'utf8')
  }

  async save(input: TemplateInput): Promise<TemplateMeta> {
    if (!ID_OK.test(input.id)) throw new Error(`非法模板 id：${input.id}`)
    await mkdir(this.dir, { recursive: true })
    const index = await this.readIndex()
    const meta: TemplateMeta = {
      id: input.id, name: input.name, description: input.description,
      trusted: input.trusted, placeholders: input.placeholders,
      updatedAt: new Date().toISOString(),
    }
    await writeFile(this.bodyPath(input.id), input.body, 'utf8')
    const rest = index.templates.filter((t) => t.id !== input.id)
    await this.writeIndex([...rest, meta])
    return meta
  }

  async remove(id: string): Promise<void> {
    await rm(this.bodyPath(id), { force: true })
    const index = await this.readIndex()
    await this.writeIndex(index.templates.filter((t) => t.id !== id))
  }

  async setTrusted(id: string, trusted: boolean): Promise<void> {
    const index = await this.readIndex()
    const target = index.templates.find((t) => t.id === id)
    if (target === undefined) throw new Error(`模板不存在：${id}`)
    target.trusted = trusted
    await this.writeIndex(index.templates)
  }
}
```

- [ ] **步骤 4：运行测试验证通过**

运行：`npx vitest run src/engine/template-store/`
预期：PASS（渲染 4 例 + 模板库 5 例）

- [ ] **步骤 5：Commit**

```bash
git add src/engine/template-store/
git commit -m "feat(template): 内置三套模板与模板库持久化

内置模板覆盖单命令/参数解析/日志与错误处理；模板库 index.json 可重建，
目录里手放的 .tpl.sh 会被自动登记。"
```

---

### 任务 4：运行目录与 run-store

**文件：**
- 创建：`src/engine/run-store/layout.ts`、`src/engine/run-store/store.ts`
- 测试：`src/engine/run-store/store.test.ts`

- [ ] **步骤 1：写失败的测试**

```ts
// src/engine/run-store/store.test.ts
import { mkdtemp, readFile } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { beforeEach, describe, expect, it } from 'vitest'
import { attemptDir, makeRunId, runDirFor } from './layout.js'
import { RunStore } from './store.js'

let root: string
beforeEach(async () => { root = await mkdtemp(join(tmpdir(), 'tu-run-')) })

describe('layout', () => {
  it('运行 id 不含路径分隔符且按时间可排序', () => {
    const id = makeRunId(new Date('2026-09-17T12:00:00Z'))
    expect(id).toMatch(/^20260917-120000-[a-z0-9]{4}$/)
    expect(id).not.toContain('/')
  })

  it('目录布局符合规格 §10', () => {
    const rd = runDirFor(root, 'r1')
    expect(attemptDir(rd, 2)).toBe(join(rd, 'attempts', '2'))
  })
})

describe('RunStore', () => {
  it('writeScript 写入运行目录根下的 script.sh 且强制 LF', async () => {
    const store = new RunStore(runDirFor(root, 'r1'))
    const p = await store.writeScript(1, 'echo hi\r\necho bye\r\n')
    expect(p.endsWith('script.sh')).toBe(true)
    expect(await readFile(p, 'utf8')).toBe('echo hi\necho bye\n')
  })

  it('writeAttempt 把每轮文件落到 attempts/<n>/ 并保留上一轮', async () => {
    const store = new RunStore(runDirFor(root, 'r1'))
    await store.writeScript(1, 'echo one\n')
    await store.writeAttempt(1, { 'shellcheck.json': '{"comments":[]}', 'stdout.txt': 'one\n' })
    await store.writeScript(2, 'echo two\n')
    await store.writeAttempt(2, { 'stdout.txt': 'two\n' })
    expect(await readFile(join(runDirFor(root, 'r1'), 'attempts', '1', 'stdout.txt'), 'utf8')).toBe('one\n')
    expect(await readFile(join(runDirFor(root, 'r1'), 'attempts', '1', 'script.sh'), 'utf8')).toBe('echo one\n')
  })

  it('meta.json 的多次 patch 会合并', async () => {
    const store = new RunStore(runDirFor(root, 'r1'))
    await store.writeMeta({ runId: 'r1' })
    await store.writeMeta({ outcome: 'succeeded', rounds: 2 })
    const meta = JSON.parse(await readFile(join(runDirFor(root, 'r1'), 'meta.json'), 'utf8'))
    expect(meta).toMatchObject({ runId: 'r1', outcome: 'succeeded', rounds: 2 })
  })
})
```

- [ ] **步骤 2：运行测试验证失败**

运行：`npx vitest run src/engine/run-store/`
预期：FAIL，`Failed to resolve import "./layout.js"`

- [ ] **步骤 3：写最小实现**

```ts
// src/engine/run-store/layout.ts
import { join } from 'node:path'
import { randomBytes } from 'node:crypto'

export function makeRunId(at: Date = new Date(), salt: string = randomBytes(2).toString('hex')): string {
  const pad = (n: number) => String(n).padStart(2, '0')
  // 用 UTC 取值：否则同一条测试在不同时区会得到不同的 id 前缀。
  const stamp = `${at.getUTCFullYear()}${pad(at.getUTCMonth() + 1)}${pad(at.getUTCDate())}-${pad(at.getUTCHours())}${pad(at.getUTCMinutes())}${pad(at.getUTCSeconds())}`
  return `${stamp}-${salt}`
}

export function runDirFor(runRoot: string, runId: string): string {
  return join(runRoot, runId)
}

export function attemptDir(runDir: string, round: number): string {
  return join(runDir, 'attempts', String(round))
}

export const AGENT_RELATIVE_PATH = join('.opencode', 'agents', 'tu-shell-writer.md')
```

```ts
// src/engine/run-store/store.ts
import { mkdir, readFile, writeFile } from 'node:fs/promises'
import { join } from 'node:path'
import type { RunStorePort } from '../ports.js'
import { toLf } from '../template-store/render.js'
import { attemptDir } from './layout.js'

export class RunStore implements RunStorePort {
  constructor(readonly runDir: string) {}

  async init(): Promise<void> {
    await mkdir(this.runDir, { recursive: true })
    await mkdir(join(this.runDir, '.opencode', 'agents'), { recursive: true })
  }

  async writeScript(round: number, script: string): Promise<string> {
    const text = toLf(script)
    const rootPath = join(this.runDir, 'script.sh')
    const perRound = join(attemptDir(this.runDir, round), 'script.sh')
    await mkdir(attemptDir(this.runDir, round), { recursive: true })
    await writeFile(rootPath, text, 'utf8')
    await writeFile(perRound, text, 'utf8')
    return rootPath
  }

  async writeAttempt(round: number, files: Record<string, string>): Promise<void> {
    const dir = attemptDir(this.runDir, round)
    await mkdir(dir, { recursive: true })
    for (const [name, content] of Object.entries(files)) {
      await writeFile(join(dir, name), content, 'utf8')
    }
  }

  async writeMeta(patch: Record<string, unknown>): Promise<void> {
    const path = join(this.runDir, 'meta.json')
    let current: Record<string, unknown> = {}
    try {
      current = JSON.parse(await readFile(path, 'utf8')) as Record<string, unknown>
    } catch { /* 首次写入 */ }
    await writeFile(path, `${JSON.stringify({ ...current, ...patch }, null, 2)}\n`, 'utf8')
  }
}
```

- [ ] **步骤 4：运行测试验证通过**

运行：`npx vitest run src/engine/run-store/`
预期：PASS（3 + 3 例）

- [ ] **步骤 5：Commit**

```bash
git add src/engine/run-store/
git commit -m "feat(run-store): 运行目录布局与每轮落盘

script.sh 在运行目录根与 attempts/<n>/ 双写；meta.json 支持增量 patch；
写盘统一 LF 归一化。"
```

---

### 任务 5：环境探测

**文件：**
- 创建：`src/engine/shell-toolchain/detect.ts`
- 测试：`src/engine/shell-toolchain/detect.test.ts`

- [ ] **步骤 1：写失败的测试**

```ts
// src/engine/shell-toolchain/detect.test.ts
import { describe, expect, it } from 'vitest'
import { candidatePaths, detectAll, parseVersion } from './detect.js'

const exists = (set: string[]) => async (p: string) => set.includes(p)
const which = (map: Record<string, string>) => async (name: string) => map[name]
const version = async (p: string) => (p.includes('shellcheck') ? 'ShellCheck - shell script analysis tool\nversion: 0.11.0' : 'bash 5.2.37')

describe('detect', () => {
  it('Linux 下 bash/shellcheck 从 PATH 解析', async () => {
    const report = await detectAll({
      platform: 'linux',
      exists: exists([]),
      which: which({ bash: '/usr/bin/bash', shellcheck: '/usr/bin/shellcheck', opencode: '/usr/bin/opencode' }),
      runVersion: version,
    })
    expect(report.bash?.path).toBe('/usr/bin/bash')
    expect(report.shellcheck?.version).toContain('0.11.0')
    expect(report.problems).toHaveLength(0)
  })

  it('Windows 下按候选顺序找到 Git Bash', async () => {
    const gitBash = 'C:/Program Files/Git/bin/bash.exe'
    const report = await detectAll({
      platform: 'win32',
      exists: exists([gitBash]),
      which: which({}),
      runVersion: async () => 'GNU bash, version 5.2.37(1)-release',
    })
    expect(report.bash?.path).toBe(gitBash)
  })

  it('缺失项写入 problems 且不抛异常', async () => {
    const report = await detectAll({
      platform: 'win32', exists: exists([]), which: which({}), runVersion: async () => '',
    })
    expect(report.opencode).toBeUndefined()
    expect(report.problems.join('\n')).toMatch(/opencode/)
    expect(report.problems.join('\n')).toMatch(/Git Bash/)
    expect(report.problems.join('\n')).toMatch(/shellcheck/)
  })

  it('配置项优先于自动探测', async () => {
    const report = await detectAll({
      platform: 'linux',
      exists: exists(['/opt/custom/bash']),
      which: which({ bash: '/usr/bin/bash' }),
      runVersion: version,
      overrides: { bashPath: '/opt/custom/bash' },
    })
    expect(report.bash?.path).toBe('/opt/custom/bash')
  })

  it('candidatePaths 在 Windows 上包含 Program Files 两个位置', () => {
    expect(candidatePaths('win32', 'bash')).toContain('C:/Program Files/Git/bin/bash.exe')
    expect(candidatePaths('win32', 'bash')).toContain('C:/Program Files (x86)/Git/bin/bash.exe')
  })
})

describe('parseVersion', () => {
  it('从 shellcheck 输出里取版本号', () => {
    expect(parseVersion('shellcheck', 'ShellCheck - shell script analysis tool\nversion: 0.11.0')).toBe('0.11.0')
  })
  it('从 bash 输出里取版本号', () => {
    expect(parseVersion('bash', 'GNU bash, version 5.2.37(1)-release (x86_64-pc-linux-gnu)')).toBe('5.2.37')
  })
})
```

- [ ] **步骤 2：运行测试验证失败**

运行：`npx vitest run src/engine/shell-toolchain/detect.test.ts`
预期：FAIL，`Failed to resolve import "./detect.js"`

- [ ] **步骤 3：写最小实现**

```ts
// src/engine/shell-toolchain/detect.ts
import { execFile } from 'node:child_process'
import { access, constants } from 'node:fs/promises'
import { join } from 'node:path'
import { promisify } from 'node:util'
import type { DetectedTool, DetectionReport } from '../types.js'

const run = promisify(execFile)

export type ToolName = 'opencode' | 'bash' | 'shellcheck'

export interface DetectDeps {
  platform: NodeJS.Platform | string
  exists: (path: string) => Promise<boolean>
  which: (name: string) => Promise<string | undefined>
  runVersion: (path: string) => Promise<string>
  overrides?: Partial<Record<'opencodePath' | 'bashPath' | 'shellcheckPath', string>>
}

/** Windows 上的固定候选位置（Git for Windows 的两种安装路径）。 */
export function candidatePaths(platform: string, tool: ToolName): string[] {
  if (platform !== 'win32') return []
  if (tool === 'bash') {
    return ['C:/Program Files/Git/bin/bash.exe', 'C:/Program Files (x86)/Git/bin/bash.exe']
  }
  if (tool === 'opencode') {
    const appData = process.env['APPDATA'] ?? ''
    return [join(appData, 'npm', 'opencode.cmd'), join(appData, 'npm', 'opencode')]
  }
  return []
}

export function parseVersion(tool: ToolName, output: string): string {
  if (tool === 'shellcheck') return /version:\s*([0-9][^\s]*)/.exec(output)?.[1] ?? 'unknown'
  if (tool === 'bash') return /version\s+([0-9][^\s(]*)/.exec(output)?.[1] ?? 'unknown'
  return /([0-9]+\.[0-9]+\.[0-9]+)/.exec(output)?.[1] ?? 'unknown'
}

function versionArgs(tool: ToolName): string[] {
  return ['--version']
}

async function resolveOne(tool: ToolName, deps: DetectDeps): Promise<DetectedTool | undefined> {
  const override = deps.overrides?.[`${tool}Path` as const]
  const candidates: string[] = []
  if (override !== undefined) candidates.push(override)
  for (const p of candidatePaths(String(deps.platform), tool)) candidates.push(p)
  const fromPath = await deps.which(tool)
  if (fromPath !== undefined) candidates.push(fromPath)

  for (const path of candidates) {
    if (!(await deps.exists(path))) continue
    const out = await deps.runVersion(path)
    return { path, version: parseVersion(tool, out) }
  }
  return undefined
}

export async function detectAll(deps: DetectDeps): Promise<DetectionReport> {
  const [opencode, bash, shellcheck] = await Promise.all([
    resolveOne('opencode', deps),
    resolveOne('bash', deps),
    resolveOne('shellcheck', deps),
  ])
  const problems: string[] = []
  if (opencode === undefined) {
    problems.push('未找到 opencode：请安装 Windows 原生 opencode（choco install opencode / scoop install opencode / npm i -g opencode-ai），本应用不支持仅存在于 WSL 的安装')
  } else if (!isAtLeast(opencode.version, '1.1.1')) {
    problems.push(`opencode 版本过低（${opencode.version}）：需要 ≥ 1.1.1 才有 permission 配置`)
  }
  if (bash === undefined) {
    problems.push('未找到 Git Bash：请安装 Git for Windows（提供 bash.exe）')
  }
  if (shellcheck === undefined) {
    problems.push('未找到 shellcheck：winget install --id koalaman.shellcheck，或使用官方 release zip')
  }
  const report: DetectionReport = { problems }
  if (opencode !== undefined) report.opencode = opencode
  if (bash !== undefined) report.bash = bash
  if (shellcheck !== undefined) report.shellcheck = shellcheck
  return report
}

/** 语义化版本比较，只比较前三段数字。 */
export function isAtLeast(version: string, minimum: string): boolean {
  const parts = (v: string) => v.split('.').map((n) => Number.parseInt(n, 10) || 0)
  const [a, b] = [parts(version), parts(minimum)]
  for (let i = 0; i < 3; i += 1) {
    const x = a[i] ?? 0
    const y = b[i] ?? 0
    if (x !== y) return x > y
  }
  return true
}

/** 生产环境的依赖实现。 */
export function systemDetectDeps(overrides?: DetectDeps['overrides']): DetectDeps {
  return {
    platform: process.platform,
    exists: async (p) => { try { await access(p, constants.X_OK); return true } catch { return false } },
    which: async (name) => {
      const finder = process.platform === 'win32' ? 'where' : 'which'
      try {
        const { stdout } = await run(finder, [name])
        return stdout.split(/\r?\n/).map((l) => l.trim()).filter(Boolean)[0]
      } catch { return undefined }
    },
    runVersion: async (p) => {
      const tool: ToolName = p.includes('shellcheck') ? 'shellcheck' : p.includes('opencode') ? 'opencode' : 'bash'
      try {
        const { stdout } = await run(p, versionArgs(tool))
        return stdout
      } catch (error) {
        const e = error as { stdout?: string; stderr?: string }
        return `${e.stdout ?? ''}\n${e.stderr ?? ''}`
      }
    },
    ...(overrides !== undefined ? { overrides } : {}),
  }
}
```

- [ ] **步骤 4：运行测试验证通过**

运行：`npx vitest run src/engine/shell-toolchain/detect.test.ts`
预期：PASS（5 + 2 例）

- [ ] **步骤 5：Commit**

```bash
git add src/engine/shell-toolchain/detect.ts src/engine/shell-toolchain/detect.test.ts
git commit -m "feat(toolchain): 环境探测与缺失项诊断

平台分支集中在 candidatePaths；缺失项汇总为中文可执行指引，不抛异常。
opencode 版本低于 1.1.1 时明确告警（permission 配置的前提）。"
```

---

### 任务 6：shellcheck 封装

**文件：**
- 创建：`src/engine/shell-toolchain/shellcheck.ts`
- 测试：`src/engine/shell-toolchain/shellcheck.test.ts`

- [ ] **步骤 1：写失败的测试**

```ts
// src/engine/shellcheck 的测试（真实调用本机 shellcheck）
import { mkdtemp, writeFile } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { beforeEach, describe, expect, it } from 'vitest'
import { ShellcheckError, buildShellcheckEnv, parseJson1, runShellcheck } from './shellcheck.js'

let dir: string
beforeEach(async () => { dir = await mkdtemp(join(tmpdir(), 'tu-sc-')) })

const SHELLCHECK = process.env['TU_SHELLCHECK'] ?? 'shellcheck'

describe('parseJson1', () => {
  it('解析 comments 数组', () => {
    const json = JSON.stringify({ comments: [{ file: 'a.sh', line: 3, column: 7, level: 'warning', code: 2086, message: 'Double quote' }] })
    expect(parseJson1(json)).toEqual([{ code: 'SC2086', line: 3, column: 7, level: 'warning', message: 'Double quote' }])
  })
  it('空 comments 返回空数组', () => {
    expect(parseJson1('{"comments":[]}')).toEqual([])
  })
})

describe('buildShellcheckEnv', () => {
  it('删除 SHELLCHECK_OPTS，避免用户环境污染结果', () => {
    const env = buildShellcheckEnv({ PATH: '/usr/bin', SHELLCHECK_OPTS: '--exclude=SC2086' })
    expect(env['SHELLCHECK_OPTS']).toBeUndefined()
    expect(env['PATH']).toBe('/usr/bin')
  })
})

describe('runShellcheck（真实调用）', () => {
  it('干净脚本退出码 0 且无发现', async () => {
    const p = join(dir, 'ok.sh')
    await writeFile(p, '#!/usr/bin/env bash\nset -euo pipefail\necho "hi"\n')
    const r = await runShellcheck(SHELLCHECK, p)
    expect(r.exitCode).toBe(0)
    expect(r.findings).toEqual([])
  })

  it('未加引号的变量产生 SC2086 且退出码 1（1 不是失败）', async () => {
    const p = join(dir, 'bad.sh')
    await writeFile(p, '#!/usr/bin/env bash\nf="a b"\nls $f\n')
    const r = await runShellcheck(SHELLCHECK, p)
    expect(r.exitCode).toBe(1)
    expect(r.findings.some((f) => f.code === 'SC2086')).toBe(true)
  })

  it('语法错误产生 error 级发现（退出码仍为 1）', async () => {
    const p = join(dir, 'syntax.sh')
    await writeFile(p, '#!/usr/bin/env bash\nif [ 1 -eq 1 ]; then\n  echo hi\n')
    const r = await runShellcheck(SHELLCHECK, p)
    expect(r.findings.some((f) => f.level === 'error')).toBe(true)
  })

  it('文件不存在时 shellcheck 退出码 2 → 判为依赖错误而非脚本问题', async () => {
    await expect(runShellcheck(SHELLCHECK, join(dir, 'nope.sh')))
      .rejects.toThrowError(ShellcheckError)
  })

  it('调用参数错误（未知 flag）退出码 3 → 抛出并携带完整命令', async () => {
    await expect(runShellcheck(SHELLCHECK, join(dir, 'ok.sh'), { extraArgs: ['--bogus-flag'] }))
      .rejects.toThrowError(/shellcheck/)
  })
})
```

前置：本测试需要本机已装 shellcheck（见"前置准备"）。可用 `TU_SHELLCHECK=/path/to/shellcheck` 覆盖。

- [ ] **步骤 2：运行测试验证失败**

运行：`npx vitest run src/engine/shell-toolchain/shellcheck.test.ts`
预期：FAIL，`Failed to resolve import "./shellcheck.js"`

- [ ] **步骤 3：写最小实现**

```ts
// src/engine/shell-toolchain/shellcheck.ts
import { execFile } from 'node:child_process'
import { promisify } from 'node:util'
import type { Severity, ShellcheckFinding } from '../types.js'

const run = promisify(execFile)

export class ShellcheckError extends Error {
  constructor(message: string, readonly exitCode: number | null, readonly command: string, readonly stderr: string) {
    super(message)
    this.name = 'ShellcheckError'
  }
}

interface Json1Comment {
  file: string
  line: number
  column: number
  level: string
  code: number
  message: string
}

const LEVELS: Severity[] = ['error', 'warning', 'info', 'style']

export function parseJson1(stdout: string): ShellcheckFinding[] {
  const parsed = JSON.parse(stdout) as { comments?: Json1Comment[] }
  return (parsed.comments ?? []).map((c) => ({
    code: `SC${c.code}`,
    line: c.line,
    column: c.column,
    level: LEVELS.includes(c.level as Severity) ? (c.level as Severity) : 'warning',
    message: c.message,
  }))
}

/** 必须删除 SHELLCHECK_OPTS：它会被隐式前置，污染结果。 */
export function buildShellcheckEnv(env: NodeJS.ProcessEnv): NodeJS.ProcessEnv {
  const copy = { ...env }
  delete copy['SHELLCHECK_OPTS']
  return copy
}

export interface ShellcheckRun {
  findings: ShellcheckFinding[]
  exitCode: number
  raw: string
  command: string
}

/**
 * 固定参数：--norc（不读用户 .shellcheckrc）、-s bash（Git Bash 即 bash）、-f json1。
 * 退出码语义（规格 §11）：0 干净 / 1 有问题 / 2 文件无法处理 / 3、4 调用错误。
 */
export async function runShellcheck(
  shellcheckPath: string,
  scriptPath: string,
  opts: { extraArgs?: string[] } = {},
): Promise<ShellcheckRun> {
  const args = ['--norc', '-s', 'bash', '-f', 'json1', ...(opts.extraArgs ?? []), '--', scriptPath]
  const command = [shellcheckPath, ...args].join(' ')
  let stdout = ''
  let stderr = ''
  let exitCode: number | null = 0
  try {
    const r = await run(shellcheckPath, args, { env: buildShellcheckEnv(process.env), maxBuffer: 8 * 1024 * 1024 })
    stdout = r.stdout
    stderr = r.stderr
  } catch (error) {
    const e = error as { code?: number; stdout?: string; stderr?: string }
    exitCode = typeof e.code === 'number' ? e.code : null
    stdout = e.stdout ?? ''
    stderr = e.stderr ?? ''
  }

  if (exitCode === 0 || exitCode === 1) {
    return { findings: parseJson1(stdout), exitCode, raw: stdout, command }
  }
  const hint = exitCode === 2 ? '文件无法处理' : exitCode === 3 ? '参数语法错误' : exitCode === 4 ? '未知 formatter/选项' : 'shellcheck 异常退出'
  throw new ShellcheckError(`shellcheck 调用失败（退出码 ${String(exitCode)}，${hint}）：${command}`, exitCode, command, stderr)
}
```

- [ ] **步骤 4：运行测试验证通过**

运行：`npx vitest run src/engine/shell-toolchain/shellcheck.test.ts`
预期：PASS（2 + 1 + 5 例）

- [ ] **步骤 5：Commit**

```bash
git add src/engine/shell-toolchain/shellcheck.ts src/engine/shell-toolchain/shellcheck.test.ts
git commit -m "feat(toolchain): shellcheck 封装与退出码语义

固定 --norc -s bash -f json1；清理 SHELLCHECK_OPTS；退出码 1 视为'有问题'
而非失败，2/3/4 抛 ShellcheckError 并携带完整命令。"
```

---

### 任务 7：脚本执行与进程树终止

**文件：**
- 创建：`src/engine/shell-toolchain/execute.ts`
- 测试：`src/engine/shell-toolchain/execute.test.ts`

- [ ] **步骤 1：写失败的测试**

```ts
// src/engine/shell-toolchain/execute.test.ts
import { mkdtemp, writeFile } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { beforeEach, describe, expect, it } from 'vitest'
import { killTree, runScript } from './execute.js'

let dir: string
beforeEach(async () => { dir = await mkdtemp(join(tmpdir(), 'tu-ex-')) })

const BASH = process.env['TU_BASH'] ?? '/usr/bin/bash'

describe('runScript（真实调用 bash）', () => {
  it('捕获 stdout、stderr 与退出码 0', async () => {
    const p = join(dir, 'ok.sh')
    await writeFile(p, 'echo 到标准输出\necho 到标准错误 >&2\nexit 0\n')
    const r = await runScript(BASH, p, { cwd: dir, timeoutMs: 5000 })
    expect(r.stdout.trim()).toBe('到标准输出')
    expect(r.stderr.trim()).toBe('到标准错误')
    expect(r.exitCode).toBe(0)
    expect(r.timedOut).toBe(false)
  })

  it('非零退出码被如实返回，不抛异常', async () => {
    const p = join(dir, 'fail.sh')
    await writeFile(p, 'echo boom >&2\nexit 7\n')
    const r = await runScript(BASH, p, { cwd: dir, timeoutMs: 5000 })
    expect(r.exitCode).toBe(7)
  })

  it('超时后杀掉进程并标记 timedOut', async () => {
    const p = join(dir, 'slow.sh')
    await writeFile(p, 'sleep 30\n')
    const started = Date.now()
    const r = await runScript(BASH, p, { cwd: dir, timeoutMs: 400 })
    expect(r.timedOut).toBe(true)
    expect(Date.now() - started).toBeLessThan(5000)
  })

  it('取消信号生效并标记 cancelled', async () => {
    const p = join(dir, 'long.sh')
    await writeFile(p, 'sleep 30\n')
    const ac = new AbortController()
    setTimeout(() => ac.abort(), 300)
    const r = await runScript(BASH, p, { cwd: dir, timeoutMs: 30_000, signal: ac.signal })
    expect(r.cancelled).toBe(true)
  })

  it('流式回调按顺序收到输出', async () => {
    const p = join(dir, 'stream.sh')
    await writeFile(p, 'echo one\nsleep 0.2\necho two\n')
    const chunks: string[] = []
    await runScript(BASH, p, { cwd: dir, timeoutMs: 5000, onStdout: (c) => chunks.push(c) })
    expect(chunks.join('')).toContain('one')
    expect(chunks.join('')).toContain('two')
  })
})

describe('killTree', () => {
  it('对已结束的进程不抛异常', async () => {
    await expect(killTree(999_999)).resolves.toBeUndefined()
  })
})
```

- [ ] **步骤 2：运行测试验证失败**

运行：`npx vitest run src/engine/shell-toolchain/execute.test.ts`
预期：FAIL，`Failed to resolve import "./execute.js"`

- [ ] **步骤 3：写最小实现**

```ts
// src/engine/shell-toolchain/execute.ts
import { spawn } from 'node:child_process'
import type { ExecuteResult } from '../types.js'

export interface RunScriptOptions {
  cwd: string
  timeoutMs: number
  signal?: AbortSignal
  onStdout?: (chunk: string) => void
  onStderr?: (chunk: string) => void
}

/** Windows 用 taskkill 杀整棵树；类 Unix 用进程组（spawn 时 detached）。 */
export async function killTree(pid: number): Promise<void> {
  if (process.platform === 'win32') {
    await new Promise<void>((resolve) => {
      const killer = spawn('taskkill', ['/PID', String(pid), '/T', '/F'], { windowsHide: true })
      killer.on('close', () => resolve())
      killer.on('error', () => resolve())
    })
    return
  }
  try {
    process.kill(-pid, 'SIGKILL')
  } catch {
    try { process.kill(pid, 'SIGKILL') } catch { /* 已退出 */ }
  }
}

export async function runScript(
  bashPath: string,
  scriptPath: string,
  opts: RunScriptOptions,
): Promise<ExecuteResult> {
  const startedAt = Date.now()
  const child = spawn(bashPath, ['--noprofile', '--norc', scriptPath], {
    cwd: opts.cwd,
    windowsHide: true,
    detached: process.platform !== 'win32',
    env: { ...process.env },
  })

  let stdout = ''
  let stderr = ''
  let timedOut = false
  let cancelled = false
  let settled = false

  const finish = (code: number | null, signal: NodeJS.Signals | null): ExecuteResult => ({
    exitCode: code,
    signal,
    timedOut,
    cancelled,
    durationMs: Date.now() - startedAt,
    stdout,
    stderr,
  })

  return await new Promise<ExecuteResult>((resolve) => {
    const stop = (reason: 'timeout' | 'cancel') => {
      if (settled) return
      if (reason === 'timeout') timedOut = true
      else cancelled = true
      if (child.pid !== undefined) void killTree(child.pid)
    }

    const timer = setTimeout(() => stop('timeout'), opts.timeoutMs)
    const onAbort = () => stop('cancel')
    opts.signal?.addEventListener('abort', onAbort, { once: true })

    child.stdout.on('data', (buf: Buffer) => {
      const text = buf.toString('utf8')
      stdout += text
      opts.onStdout?.(text)
    })
    child.stderr.on('data', (buf: Buffer) => {
      const text = buf.toString('utf8')
      stderr += text
      opts.onStderr?.(text)
    })
    child.on('error', (error) => {
      stderr += `\n[spawn 失败] ${error.message}\n`
    })
    child.on('close', (code, signal) => {
      settled = true
      clearTimeout(timer)
      opts.signal?.removeEventListener('abort', onAbort)
      resolve(finish(code, signal))
    })
  })
}
```

- [ ] **步骤 4：运行测试验证通过**

运行：`npx vitest run src/engine/shell-toolchain/execute.test.ts`
预期：PASS（5 + 1 例）

- [ ] **步骤 5：Commit**

```bash
git add src/engine/shell-toolchain/execute.ts src/engine/shell-toolchain/execute.test.ts
git commit -m "feat(toolchain): Git Bash 执行、超时与进程树终止

bash --noprofile --norc 执行脚本；流式回调；Windows 走 taskkill /T /F，
类 Unix 走进程组 SIGKILL。非零退出码如实返回，不抛异常。"
```

---

### 任务 8：契约校验与提示词

**文件：**
- 创建：`src/engine/orchestrator/contract.ts`、`src/engine/orchestrator/prompt.ts`
- 测试：`src/engine/orchestrator/contract.test.ts`、`src/engine/orchestrator/prompt.test.ts`

- [ ] **步骤 1：写失败的测试**

```ts
// src/engine/orchestrator/contract.test.ts
import { describe, expect, it } from 'vitest'
import { checkContract, extractAnchors, normalizeScript } from './contract.js'

describe('extractAnchors', () => {
  it('按出现顺序取出锚点', () => {
    expect(extractAnchors('a\n# @@TU:BODY@@\nb\n# @@TU:EXTRA@@\n')).toEqual(['@@TU:BODY@@', '@@TU:EXTRA@@'])
  })
})

describe('checkContract', () => {
  const anchors = ['@@TU:BODY@@']

  it('通过：内容非空、含全部锚点、无 CRLF', () => {
    const r = checkContract('#!/usr/bin/env bash\n# @@TU:BODY@@\necho hi\n', anchors)
    expect(r.ok).toBe(true)
    expect(r.missingAnchors).toEqual([])
  })

  it('空脚本失败', () => {
    expect(checkContract('   \n', anchors).reason).toBe('empty')
  })

  it('缺锚点失败并列出缺哪个', () => {
    const r = checkContract('#!/usr/bin/env bash\necho hi\n', anchors)
    expect(r.ok).toBe(false)
    expect(r.reason).toBe('missing_anchor')
    expect(r.missingAnchors).toEqual(['@@TU:BODY@@'])
  })

  it('含 CRLF 失败', () => {
    expect(checkContract('echo hi\r\n# @@TU:BODY@@\r\n', anchors).reason).toBe('has_crlf')
  })

  it('超过 64KB 失败', () => {
    const big = `# @@TU:BODY@@\n${'x'.repeat(70 * 1024)}\n`
    expect(checkContract(big, anchors).reason).toBe('too_large')
  })
})

describe('normalizeScript', () => {
  it('去掉 markdown 代码块围栏与前后空白，并归一化 LF', () => {
    const fenced = '```bash\r\n#!/usr/bin/env bash\r\n# @@TU:BODY@@\r\n```\r\n'
    expect(normalizeScript(fenced)).toBe('#!/usr/bin/env bash\n# @@TU:BODY@@\n')
  })
  it('确保结尾恰好一个换行', () => {
    expect(normalizeScript('echo hi')).toBe('echo hi\n')
  })
})
```

```ts
// src/engine/orchestrator/prompt.test.ts
import { describe, expect, it } from 'vitest'
import type { FailureEvidence } from '../types.js'
import { OUTPUT_SCHEMA, buildFirstMessage, buildRepairMessage, shellsCheckSummary } from './prompt.js'

describe('OUTPUT_SCHEMA', () => {
  it('要求 script/notes/assumptions 三个字段', () => {
    expect(OUTPUT_SCHEMA['required']).toEqual(['script', 'notes', 'assumptions'])
  })
})

describe('buildFirstMessage', () => {
  it('包含骨架、方案全文、运行目录与锚点清单', () => {
    const msg = buildFirstMessage({
      skeleton: '#!/usr/bin/env bash\n# @@TU:BODY@@\n',
      anchors: ['@@TU:BODY@@'],
      plan: '把所有 .log 文件压缩',
      runDir: '/tmp/runs/r1',
    })
    expect(msg).toContain('把所有 .log 文件压缩')
    expect(msg).toContain('@@TU:BODY@@')
    expect(msg).toContain('/tmp/runs/r1')
  })
})

describe('buildRepairMessage', () => {
  const evidence: FailureEvidence = {
    round: 1,
    stage: 'shellcheck',
    shellcheck: [{ code: 'SC2086', line: 12, column: 5, level: 'warning', message: 'Double quote to prevent globbing.' }],
  }

  it('把结构化证据与锚点清单一起给出，并要求返回完整脚本', () => {
    const msg = buildRepairMessage({ evidence, anchors: ['@@TU:BODY@@'], skeleton: '# @@TU:BODY@@\n' })
    expect(msg).toContain('SC2086')
    expect(msg).toContain('第 12 行')
    expect(msg).toContain('@@TU:BODY@@')
    expect(msg).toMatch(/完整脚本/)
  })

  it('执行失败时带上退出码与 stderr 尾部', () => {
    const execEvidence: FailureEvidence = {
      round: 2, stage: 'execute',
      execute: { exitCode: 1, timedOut: false, stdoutTail: '', stderrTail: 'no such file', durationMs: 12 },
    }
    const msg = buildRepairMessage({ evidence: execEvidence, anchors: [], skeleton: '' })
    expect(msg).toContain('退出码 1')
    expect(msg).toContain('no such file')
  })
})

describe('shellsCheckSummary', () => {
  it('按编号汇总', () => {
    const s = shellsCheckSummary([
      { code: 'SC2086', line: 1, column: 1, level: 'warning', message: 'a' },
      { code: 'SC2086', line: 9, column: 1, level: 'warning', message: 'b' },
      { code: 'SC2045', line: 3, column: 1, level: 'warning', message: 'c' },
    ])
    expect(s).toContain('SC2086 ×2')
    expect(s).toContain('SC2045 ×1')
  })
})
```

- [ ] **步骤 2：运行测试验证失败**

运行：`npx vitest run src/engine/orchestrator/`
预期：FAIL，`Failed to resolve import "./contract.js"`

- [ ] **步骤 3：写最小实现**

```ts
// src/engine/orchestrator/contract.ts
import type { ContractResult } from '../types.js'

export const MAX_SCRIPT_BYTES = 64 * 1024

const ANCHOR_PATTERN = /#\s*(@@TU:[A-Z0-9_]+@@)/g

export function extractAnchors(text: string): string[] {
  return [...text.matchAll(ANCHOR_PATTERN)].map((m) => m[1]!)
}

/** 去围栏、去首尾空白、归一化 LF、结尾恰好一个换行（规格 §7.3、§11）。 */
export function normalizeScript(raw: string): string {
  let text = raw.replace(/\r\n?/g, '\n').trim()
  const fence = /^```[a-zA-Z]*\n([\s\S]*?)\n?```$/.exec(text)
  if (fence !== null) text = fence[1]!.replace(/\r\n?/g, '\n').trim()
  return `${text}\n`
}

export function checkContract(normalized: string, requiredAnchors: string[]): ContractResult {
  const bytes = Buffer.byteLength(normalized, 'utf8')
  const missingAnchors = requiredAnchors.filter((a) => !normalized.includes(a))
  const base = { missingAnchors, bytes }

  if (normalized.trim().length === 0) return { ok: false, reason: 'empty', ...base }
  if (bytes > MAX_SCRIPT_BYTES) return { ok: false, reason: 'too_large', ...base }
  if (/\r/.test(normalized)) return { ok: false, reason: 'has_crlf', ...base }
  if (missingAnchors.length > 0) return { ok: false, reason: 'missing_anchor', ...base }
  return { ok: true, ...base }
}
```

```ts
// src/engine/orchestrator/prompt.ts
import type { FailureEvidence, ShellcheckFinding } from '../types.js'

export const OUTPUT_SCHEMA: Record<string, unknown> = {
  type: 'object',
  additionalProperties: false,
  required: ['script', 'notes', 'assumptions'],
  properties: {
    script: { type: 'string', description: '完整的 shell 脚本内容；必须保留模板锚点注释；LF 换行；不要包含 markdown 代码块围栏' },
    notes: { type: 'string', description: '做了哪些取舍；方案中含糊之处如何处理' },
    assumptions: { type: 'array', items: { type: 'string' }, description: '你假定的前提' },
  },
}

export const SYSTEM_RULES = `你是一个 shell 脚本生成器。用户会给你一份模板骨架和一份方案文档，你把方案实现进骨架。

硬规则：
1. 保留模板里的全部锚点注释（形如 # @@TU:NAME@@），一个都不能少、不能改名。
2. 保持模板的整体结构（shebang、set 选项、函数骨架、trap、参数解析）。
3. 不要引入网络下载、提权（sudo）、curl | bash、交互式命令。
4. 换行必须是 LF。
5. 只在返回 JSON 的 script 字段里给出完整脚本，不要额外解释。
6. 方案含糊时选择保守实现，并把假设写进 assumptions，不要静默猜测。`

export function buildFirstMessage(input: {
  skeleton: string
  anchors: string[]
  plan: string
  runDir: string
}): string {
  return [
    '## 模板骨架（必须保留结构）',
    '```bash',
    input.skeleton.trimEnd(),
    '```',
    '',
    '## 必须保留的锚点',
    input.anchors.map((a) => `- ${a}`).join('\n') || '（本模板无锚点）',
    '',
    '## 方案文档',
    input.plan.trim(),
    '',
    `## 运行目录（脚本将在此目录下执行）`,
    input.runDir,
    '',
    '请按照上述硬规则，把方案实现进骨架，返回完整脚本。',
  ].join('\n')
}

export function shellsCheckSummary(findings: ShellcheckFinding[]): string {
  const counts = new Map<string, number>()
  for (const f of findings) counts.set(f.code, (counts.get(f.code) ?? 0) + 1)
  return [...counts.entries()].sort((a, b) => b[1] - a[1]).map(([code, n]) => `${code} ×${n}`).join('、')
}

export function buildRepairMessage(input: {
  evidence: FailureEvidence
  anchors: string[]
  skeleton: string
}): string {
  const { evidence } = input
  const lines: string[] = [`## 第 ${evidence.round} 轮失败反馈（阶段：${evidence.stage}）`, '']

  if (evidence.contract !== undefined) {
    const c = evidence.contract
    if (c.message !== undefined) {
      lines.push(`上一轮没有产出可用脚本：${c.message}`)
      lines.push('请重新返回符合 schema 的 JSON（script 字段必须是完整脚本）。')
    } else {
      lines.push(`契约校验未通过：${c.reason}`)
      if (c.missingAnchors.length > 0) {
        lines.push(`缺失的锚点：${c.missingAnchors.join('、')}`)
      }
    }
    lines.push('')
  }

  if (evidence.shellcheck !== undefined && evidence.shellcheck.length > 0) {
    lines.push(`shellcheck 汇总：${shellsCheckSummary(evidence.shellcheck)}`, '')
    for (const f of evidence.shellcheck) {
      lines.push(`- ${f.code} 第 ${f.line} 行（${f.level}）：${f.message}`)
    }
    lines.push('')
  }

  if (evidence.execute !== undefined) {
    const e = evidence.execute
    lines.push(`执行失败：退出码 ${String(e.exitCode)}${e.timedOut ? '（超时被杀）' : ''}，耗时 ${e.durationMs}ms`, '')
    if (e.stderrTail.trim() !== '') lines.push('stderr 尾部：', '```', e.stderrTail.trimEnd(), '```', '')
    if (e.stdoutTail.trim() !== '') lines.push('stdout 尾部：', '```', e.stdoutTail.trimEnd(), '```', '')
  }

  if (input.skeleton.trim() !== '') {
    lines.push('## 模板骨架（结构不得改动）', '```bash', input.skeleton.trimEnd(), '```', '')
  }
  lines.push('## 必须保留的锚点', input.anchors.map((a) => `- ${a}`).join('\n') || '（无）', '')
  lines.push('只修复上述问题，保持锚点与模板结构不变，返回完整脚本。')
  return lines.join('\n')
}
```

- [ ] **步骤 4：运行测试验证通过**

运行：`npx vitest run src/engine/orchestrator/`
预期：PASS（契约 8 例 + 提示词 5 例）

- [ ] **步骤 5：Commit**

```bash
git add src/engine/orchestrator/contract.ts src/engine/orchestrator/prompt.ts src/engine/orchestrator/*.test.ts
git commit -m "feat(orchestrator): 契约校验与提示词组装

契约覆盖 empty/too_large/has_crlf/missing_anchor 四种失败；提示词把方案、
锚点、运行目录与结构化失败证据拼成对模型友好的输入。"
```

---

### 任务 9：agent 定义生成

**文件：**
- 创建：`src/engine/opencode-adapter/agent-file.ts`
- 测试：`src/engine/opencode-adapter/agent-file.test.ts`

- [ ] **步骤 1：写失败的测试**

```ts
// src/engine/opencode-adapter/agent-file.test.ts
import { describe, expect, it } from 'vitest'
import { AGENT_NAME, renderAgentFile, writeAgentFile } from './agent-file.js'

describe('renderAgentFile', () => {
  it('逐键 deny，且只给运行目录只读权限', () => {
    const md = renderAgentFile({ runDir: 'C:/Users/me/runs/r1' })
    for (const key of ['bash', 'edit', 'webfetch', 'websearch', 'task', 'skill', 'glob', 'grep', 'list', 'lsp']) {
      expect(md).toMatch(new RegExp(`^\\s*${key}: deny$`, 'm'))
    }
    expect(md).toContain('external_directory: deny')
    expect(md).toContain('"C:/Users/me/runs/r1/**": allow')
  })

  it('不包含 model 字段（沿用用户全局模型）', () => {
    expect(renderAgentFile({ runDir: '/tmp/r1' })).not.toMatch(/^model:/m)
  })

  it('指定 model 时才写入该字段', () => {
    expect(renderAgentFile({ runDir: '/tmp/r1', model: 'anthropic/claude-sonnet-4' }))
      .toMatch(/^model: anthropic\/claude-sonnet-4$/m)
  })

  it('反斜杠路径被转成正斜杠，避免 YAML 转义问题', () => {
    expect(renderAgentFile({ runDir: 'C:\\Users\\me\\runs\\r1' })).toContain('C:/Users/me/runs/r1/**')
  })

  it('frontmatter 合法：以 --- 开头并以 --- 结束', () => {
    const md = renderAgentFile({ runDir: '/tmp/r1' })
    expect(md.startsWith('---\n')).toBe(true)
    expect(md.split('\n').filter((l) => l === '---')).toHaveLength(2)
  })
})

describe('writeAgentFile', () => {
  it('写到 <runDir>/.opencode/agents/tu-shell-writer.md 并返回路径', async () => {
    const { mkdtemp } = await import('node:fs/promises')
    const { tmpdir } = await import('node:os')
    const { join } = await import('node:path')
    const dir = await mkdtemp(join(tmpdir(), 'tu-agent-'))
    const p = await writeAgentFile(dir, { runDir: dir })
    expect(p).toBe(join(dir, '.opencode', 'agents', `${AGENT_NAME}.md`))
  })
})
```

- [ ] **步骤 2：运行测试验证失败**

运行：`npx vitest run src/engine/opencode-adapter/agent-file.test.ts`
预期：FAIL，`Failed to resolve import "./agent-file.js"`

- [ ] **步骤 3：写最小实现**

```ts
// src/engine/opencode-adapter/agent-file.ts
import { mkdir, writeFile } from 'node:fs/promises'
import { dirname, join } from 'node:path'
import { SYSTEM_RULES } from '../orchestrator/prompt.js'

export const AGENT_NAME = 'tu-shell-writer'

/** Windows 路径在 YAML 里容易被转义，统一转成正斜杠。 */
function toPosix(p: string): string {
  return p.replace(/\\/g, '/').replace(/\/+$/, '')
}

/**
 * 生成项目级 agent 定义（规格 §7.2）。
 * 安全模型不押在总键上：逐键显式 deny（源码级调研只确认了具体权限键）。
 */
export function renderAgentFile(input: { runDir: string; model?: string }): string {
  const run = toPosix(input.runDir)
  const lines = [
    '---',
    'description: 把方案文档实现进给定的 shell 模板骨架；只读运行目录，不写文件、不执行命令。',
    'mode: primary',
  ]
  if (input.model !== undefined) lines.push(`model: ${input.model}`)
  lines.push(
    'permission:',
    '  bash: deny',
    '  edit: deny',
    '  glob: deny',
    '  grep: deny',
    '  list: deny',
    '  lsp: deny',
    '  skill: deny',
    '  task: deny',
    '  todowrite: deny',
    '  question: deny',
    '  webfetch: deny',
    '  websearch: deny',
    '  doom_loop: deny',
    '  external_directory: deny',
    '  read:',
    '    "*": deny',
    `    "${run}/**": allow`,
    '---',
    SYSTEM_RULES,
    '',
  )
  return lines.join('\n')
}

export async function writeAgentFile(
  runDir: string,
  input: { runDir: string; model?: string },
): Promise<string> {
  const path = join(runDir, '.opencode', 'agents', `${AGENT_NAME}.md`)
  await mkdir(dirname(path), { recursive: true })
  await writeFile(path, renderAgentFile(input), 'utf8')
  return path
}
```

- [ ] **步骤 4：运行测试验证通过**

运行：`npx vitest run src/engine/opencode-adapter/agent-file.test.ts`
预期：PASS（5 + 1 例）

- [ ] **步骤 5：Commit**

```bash
git add src/engine/opencode-adapter/agent-file.ts src/engine/opencode-adapter/agent-file.test.ts
git commit -m "feat(adapter): 生成项目级 agent 定义

逐键 deny（bash/edit/网络/子代理全禁）+ read 锁死在运行目录；
路径转正斜杠避免 YAML 转义；不写 model 字段以沿用用户全局模型。"
```

---

### 任务 10：opencode server 与客户端封装

**文件：**
- 创建：`src/engine/opencode-adapter/server.ts`、`src/engine/opencode-adapter/events.ts`、`src/engine/opencode-adapter/index.ts`
- 测试：`src/engine/opencode-adapter/events.test.ts`、`src/engine/opencode-adapter/server.test.ts`

- [ ] **步骤 1：写失败的测试**

```ts
// src/engine/opencode-adapter/events.test.ts
import { describe, expect, it } from 'vitest'
import { SseParser, eventText, isPermissionAsk } from './events.js'

describe('SseParser', () => {
  it('把分片的 SSE 数据拼成完整事件', () => {
    const parser = new SseParser()
    expect(parser.push('data: {"type":"server.connected"}\n')).toEqual([{ type: 'server.connected' }])
    const events = parser.push('data: {"type":"message.part.updated",\n')
    expect(events).toEqual([])
    expect(parser.push('data: "properties":{"x":1}}\n')).toEqual([
      { type: 'message.part.updated', properties: { x: 1 } },
    ])
  })

  it('忽略注释行与空行', () => {
    const parser = new SseParser()
    expect(parser.push(': keep-alive\n\n')).toEqual([])
  })
})

describe('eventText', () => {
  it('从 message.part.updated 的文本部件里取出增量文本', () => {
    const ev = { type: 'message.part.updated', properties: { part: { type: 'text', text: '你好' } } }
    expect(eventText(ev)).toBe('你好')
  })
  it('非文本部件返回 undefined', () => {
    expect(eventText({ type: 'message.part.updated', properties: { part: { type: 'tool', tool: 'read' } } })).toBeUndefined()
  })
})

describe('isPermissionAsk', () => {
  it('识别 permission.updated 并取出 permissionID 与 sessionID', () => {
    const ev = { type: 'permission.updated', properties: { id: 'per_1', sessionID: 'ses_1' } }
    expect(isPermissionAsk(ev)).toEqual({ permissionId: 'per_1', sessionId: 'ses_1' })
  })
  it('其它事件返回 undefined', () => {
    expect(isPermissionAsk({ type: 'session.idle', properties: {} })).toBeUndefined()
  })
})
```

```ts
// src/engine/opencode-adapter/server.test.ts
import { describe, expect, it } from 'vitest'
import { basicAuthHeader, buildServeArgs, pickFreePort } from './server.js'

describe('buildServeArgs', () => {
  it('固定绑定回环地址并显式给端口', () => {
    expect(buildServeArgs(4123)).toEqual(['serve', '--hostname', '127.0.0.1', '--port', '4123'])
  })
})

describe('basicAuthHeader', () => {
  it('按 opencode:<password> 编码', () => {
    expect(basicAuthHeader('secret')).toBe(`Basic ${Buffer.from('opencode:secret').toString('base64')}`)
  })
})

describe('pickFreePort', () => {
  it('返回可用的端口号', async () => {
    const port = await pickFreePort()
    expect(port).toBeGreaterThan(1024)
    expect(port).toBeLessThan(65536)
  })
})
```

- [ ] **步骤 2：运行测试验证失败**

运行：`npx vitest run src/engine/opencode-adapter/`
预期：FAIL，`Failed to resolve import "./events.js"`

- [ ] **步骤 3：写最小实现**

```ts
// src/engine/opencode-adapter/events.ts
export interface OpencodeEvent {
  type: string
  properties?: Record<string, unknown>
}

/**
 * opencode 的事件流是 SSE（`GET /event`），信封为 {type, properties}。
 * 自己解析而不依赖 SDK 的事件方法：SSE 是文档化的接口，行为可预期。
 */
export class SseParser {
  private buffer = ''
  private pending = ''

  /**
   * SSE 的一帧可能被拆成多个 `data:` 行，也可能被拆到多个网络分片里。
   * 策略：累积 data 行，直到拼出的字符串能被 JSON.parse 解析为止；
   * 遇到空行（事件边界）仍未解析成功则丢弃该坏帧。
   */
  push(chunk: string): OpencodeEvent[] {
    this.buffer += chunk
    const events: OpencodeEvent[] = []
    for (;;) {
      const index = this.buffer.indexOf('\n')
      if (index === -1) break
      const line = this.buffer.slice(0, index).replace(/\r$/, '')
      this.buffer = this.buffer.slice(index + 1)

      if (line.startsWith('data:')) {
        const piece = line.slice('data:'.length).trimStart()
        this.pending = this.pending === '' ? piece : `${this.pending}\n${piece}`
        const parsed = tryParse(this.pending)
        if (parsed !== undefined) {
          events.push(parsed)
          this.pending = ''
        }
      } else if (line === '' && this.pending !== '') {
        this.pending = ''
      }
      // 注释行（以 : 开头）与其它字段直接忽略
    }
    return events
  }
}

function tryParse(text: string): OpencodeEvent | undefined {
  try {
    return JSON.parse(text) as OpencodeEvent
  } catch {
    return undefined
  }
}

export function eventText(event: OpencodeEvent): string | undefined {
  const part = event.properties?.['part'] as { type?: string; text?: string } | undefined
  if (part?.type === 'text' && typeof part.text === 'string') return part.text
  return undefined
}

export function isPermissionAsk(event: OpencodeEvent): { permissionId: string; sessionId: string } | undefined {
  if (event.type !== 'permission.updated') return undefined
  const id = event.properties?.['id']
  const sessionID = event.properties?.['sessionID']
  if (typeof id !== 'string' || typeof sessionID !== 'string') return undefined
  return { permissionId: id, sessionId: sessionID }
}
```

```ts
// src/engine/opencode-adapter/server.ts
import { spawn, type ChildProcess } from 'node:child_process'
import { createServer } from 'node:net'
import { createWriteStream, type WriteStream } from 'node:fs'
import { join } from 'node:path'

export function buildServeArgs(port: number): string[] {
  return ['serve', '--hostname', '127.0.0.1', '--port', String(port)]
}

export function basicAuthHeader(password: string): string {
  return `Basic ${Buffer.from(`opencode:${password}`).toString('base64')}`
}

export async function pickFreePort(): Promise<number> {
  return await new Promise<number>((resolve, reject) => {
    const srv = createServer()
    srv.on('error', reject)
    srv.listen(0, '127.0.0.1', () => {
      const address = srv.address()
      const port = typeof address === 'object' && address !== null ? address.port : 0
      srv.close(() => { port > 0 ? resolve(port) : reject(new Error('无法分配端口')) })
    })
  })
}

export interface ServeHandle {
  baseUrl: string
  password: string
  port: number
  pid: number | undefined
  logPath: string
  stop: () => Promise<void>
}

/** 起一个独占的 opencode serve，stdout/stderr 收进运行目录的 server.log（规格 §7.1）。 */
export async function startServe(input: {
  opencodePath: string
  runDir: string
}): Promise<ServeHandle> {
  const port = await pickFreePort()
  const password = crypto.randomUUID().replace(/-/g, '')
  const logPath = join(input.runDir, 'server.log')
  const log: WriteStream = createWriteStream(logPath, { flags: 'a' })

  const child: ChildProcess = spawn(input.opencodePath, buildServeArgs(port), {
    cwd: input.runDir,
    windowsHide: true,
    env: { ...process.env, OPENCODE_SERVER_PASSWORD: password },
  })
  child.stdout?.pipe(log)
  child.stderr?.pipe(log)

  const baseUrl = `http://127.0.0.1:${port}`
  await waitHealthy(baseUrl, password, 20_000)

  return {
    baseUrl, password, port, pid: child.pid, logPath,
    stop: async () => {
      log.end()
      if (child.pid === undefined) return
      const { killTree } = await import('../shell-toolchain/execute.js')
      await killTree(child.pid)
    },
  }
}

export async function waitHealthy(baseUrl: string, password: string, timeoutMs: number): Promise<void> {
  const deadline = Date.now() + timeoutMs
  let lastError = 'unknown'
  while (Date.now() < deadline) {
    try {
      const res = await fetch(`${baseUrl}/global/health`, {
        headers: { authorization: basicAuthHeader(password) },
      })
      if (res.ok) {
        const body = (await res.json()) as { healthy?: boolean }
        if (body.healthy === true) return
        lastError = `health 返回 ${JSON.stringify(body)}`
      } else {
        lastError = `HTTP ${res.status}`
      }
    } catch (error) {
      lastError = (error as Error).message
    }
    await new Promise((r) => setTimeout(r, 250))
  }
  throw new Error(`opencode serve 在 ${timeoutMs}ms 内未就绪：${lastError}`)
}
```

```ts
// src/engine/opencode-adapter/index.ts
import { createOpencodeClient } from '@opencode-ai/sdk'
import type { OpencodePort } from '../ports.js'
import type { GeneratedScript } from '../types.js'
import { AGENT_NAME, writeAgentFile } from './agent-file.js'
import { isPermissionAsk, SseParser, eventText, type OpencodeEvent } from './events.js'
import { basicAuthHeader, startServe, type ServeHandle } from './server.js'

export interface AdapterOptions {
  opencodePath: string
  onNote?: (message: string) => void
}

/**
 * OpencodePort 的 SDK 实现。
 * - 会话与结构化输出走 SDK（文档化 JS API）；
 * - SSE 事件与权限应答走原生 fetch（HTTP 端点文档化，避免依赖未确认的 SDK 方法名）。
 */
export class OpencodeAdapter implements OpencodePort {
  private serve: ServeHandle | undefined
  private client: ReturnType<typeof createOpencodeClient> | undefined
  private runDir = ''

  constructor(private readonly options: AdapterOptions) {}

  async start(runDir: string, opts: { agentName: string; model?: string }): Promise<{ sessionId: string }> {
    this.runDir = runDir
    await writeAgentFile(runDir, opts.model !== undefined ? { runDir, model: opts.model } : { runDir })
    this.serve = await startServe({ opencodePath: this.options.opencodePath, runDir })
    const auth = basicAuthHeader(this.serve.password)
    this.client = createOpencodeClient({
      baseUrl: this.serve.baseUrl,
      fetch: ((input: RequestInfo | URL, init?: RequestInit) =>
        fetch(input, { ...init, headers: { ...(init?.headers ?? {}), authorization: auth } })) as typeof fetch,
      throwOnError: true,
    })
    void this.consumeEvents(this.serve.baseUrl, auth)
    const created = await this.client.session.create({ body: { title: `tu-shell-agent ${runDir}` } })
    return { sessionId: created.data.id }
  }

  /** 对任何权限询问一律拒绝（规格 §7.2 的安全网）。 */
  private async consumeEvents(baseUrl: string, auth: string): Promise<void> {
    const res = await fetch(`${baseUrl}/event`, { headers: { authorization: auth, accept: 'text/event-stream' } })
    if (res.body === null) return
    const parser = new SseParser()
    const reader = res.body.getReader()
    const decoder = new TextDecoder()
    for (;;) {
      const { value, done } = await reader.read()
      if (done === true) break
      for (const event of parser.push(decoder.decode(value, { stream: true }))) {
        const ask = isPermissionAsk(event)
        if (ask !== undefined) {
          await fetch(`${baseUrl}/session/${ask.sessionId}/permissions/${ask.permissionId}`, {
            method: 'POST',
            headers: { authorization: auth, 'content-type': 'application/json' },
            body: JSON.stringify({ response: 'reject' }),
          })
          this.options.onNote?.(`已自动拒绝 opencode 的权限请求 ${ask.permissionId}`)
        }
        this.currentOnDelta?.(event)
      }
    }
  }

  private currentOnDelta: ((event: OpencodeEvent) => void) | undefined

  async generate(
    sessionId: string,
    userMessage: string,
    opts: { schema: object; timeoutMs: number; onDelta?: (text: string) => void; signal?: AbortSignal },
  ): Promise<GeneratedScript> {
    if (this.client === undefined) throw new Error('适配器未启动')
    this.currentOnDelta = (event) => {
      const text = eventText(event)
      if (text !== undefined) opts.onDelta?.(text)
    }
    const timer = setTimeout(() => { void this.abort(sessionId) }, opts.timeoutMs)
    try {
      const result = await this.client.session.prompt({
        path: { id: sessionId },
        body: {
          agent: AGENT_NAME,
          parts: [{ type: 'text', text: userMessage }],
          format: { type: 'json_schema', schema: opts.schema, retryCount: 2 },
        } as never,
      })
      const info = (result as { data?: { info?: { structured_output?: unknown } } }).data?.info
      const structured = info?.structured_output
      if (structured === undefined || structured === null) {
        throw new Error('opencode 未返回 structured_output：确认 model 支持结构化输出，或改传 outputFormat（文档 §Structured Output 与 SDK 表存在不一致）')
      }
      const parsed = structured as Partial<GeneratedScript>
      if (typeof parsed.script !== 'string' || parsed.script.trim() === '') {
        throw new Error('structured_output.script 为空')
      }
      return {
        script: parsed.script,
        notes: typeof parsed.notes === 'string' ? parsed.notes : '',
        assumptions: Array.isArray(parsed.assumptions) ? parsed.assumptions.filter((a): a is string => typeof a === 'string') : [],
      }
    } finally {
      clearTimeout(timer)
      this.currentOnDelta = undefined
    }
  }

  async abort(sessionId: string): Promise<void> {
    await this.client?.session.abort({ path: { id: sessionId } })
  }

  async dispose(): Promise<void> {
    await this.serve?.stop()
    this.serve = undefined
    this.client = undefined
  }
}
```

- [ ] **步骤 4：运行测试验证通过**

运行：`npx vitest run src/engine/opencode-adapter/ && npx tsc --noEmit`
预期：PASS（events 5 例 + server 3 例），tsc 无错误

- [ ] **步骤 5：Commit**

```bash
git add src/engine/opencode-adapter/
git commit -m "feat(adapter): opencode serve 生命周期、SSE 订阅与结构化输出

每次运行独占一个 serve（随机端口 + 随机密码 + server.log）；SSE 自解析
以渲染增量文本；对任何 permission.updated 一律 reject 作为安全网；
structured_output 缺失时明确报错而不是交回空脚本。"
```

---

### 任务 11：编排状态机

**文件：**
- 创建：`src/engine/orchestrator/loop.ts`
- 测试：`src/engine/orchestrator/loop.test.ts`

- [ ] **步骤 1：写失败的测试**

```ts
// src/engine/orchestrator/loop.test.ts
import { describe, expect, it } from 'vitest'
import type { LoopPorts, OpencodePort, ToolchainPort } from '../ports.js'
import type { ExecuteResult, GeneratedScript, RunConfig, ShellcheckFinding } from '../types.js'
import { runLoop } from './loop.js'

const config: RunConfig = {
  runRoot: '/tmp/root', maxRounds: 3, generateTimeoutMs: 5000, executeTimeoutMs: 5000, blockingLevel: 'warning',
}

const skeleton = '#!/usr/bin/env bash\nset -euo pipefail\n# @@TU:BODY@@\necho ok\n'
const template = { id: 'single', body: skeleton, anchors: ['@@TU:BODY@@'], trusted: true, placeholders: [] }

interface Harness {
  ports: LoopPorts
  scripts: string[]
  written: string[]
  prompts: string[]
}

function makeHarness(input: {
  rounds: GeneratedScript[]
  shellcheckFor?: (script: string) => ShellcheckFinding[]
  executeFor?: (script: string) => ExecuteResult
  confirm?: boolean
  emit?: (e: unknown) => void
}): Harness {
  const scripts: string[] = []
  const written: string[] = []
  const prompts: string[] = []
  let turn = 0

  const opencode: OpencodePort = {
    start: async () => ({ sessionId: 'ses_1' }),
    generate: async (_id, message) => {
      prompts.push(message)
      const r = input.rounds[Math.min(turn, input.rounds.length - 1)]!
      turn += 1
      scripts.push(r.script)
      return r
    },
    abort: async () => {},
    dispose: async () => {},
  }
  const toolchain: ToolchainPort = {
    detect: async () => ({ problems: [] }),
    shellcheck: async () => {
      const current = scripts[scripts.length - 1] ?? ''
      return { findings: input.shellcheckFor?.(current) ?? [], exitCode: 0, raw: '{"comments":[]}' }
    },
    execute: async () => input.executeFor?.(scripts[scripts.length - 1] ?? '') ?? {
      exitCode: 0, signal: null, timedOut: false, cancelled: false, durationMs: 1, stdout: 'ok\n', stderr: '',
    },
  }
  const ports: LoopPorts = {
    opencode,
    toolchain,
    confirm: async () => input.confirm ?? true,
    store: {
      runDir: '/tmp/run',
      writeScript: async (round, script) => { written.push(`${round}:${script}`); return `/tmp/run/script.sh` },
      writeAttempt: async () => {},
      writeMeta: async () => {},
    },
    emit: (input.emit ?? (() => {})) as LoopPorts['emit'],
  }
  return { ports, scripts, written, prompts }
}

const goodScript = { script: skeleton, notes: '', assumptions: [] }

describe('runLoop', () => {
  it('第 1 轮就通过 → succeeded，且只写一次脚本', async () => {
    const h = makeHarness({ rounds: [goodScript] })
    const result = await runLoop({ plan: '方案', template, values: {}, runDir: '/tmp/run', config, ports: h.ports })
    expect(result.outcome).toBe('succeeded')
    expect(result.rounds).toBe(1)
    expect(h.written).toHaveLength(1)
  })

  it('第 1 轮 shellcheck 失败、第 2 轮修好 → succeeded/2，且第 2 轮提示词带 SC 编号', async () => {
    const broken = { script: '#!/usr/bin/env bash\n# @@TU:BODY@@\nf="a b"\nls $f\n', notes: '', assumptions: [] }
    const h = makeHarness({
      rounds: [broken, goodScript],
      shellcheckFor: (s) => (s.includes('ls $f') ? [{ code: 'SC2086', line: 4, column: 4, level: 'warning', message: 'Double quote' }] : []),
    })
    const result = await runLoop({ plan: '方案', template, values: {}, runDir: '/tmp/run', config, ports: h.ports })
    expect(result.outcome).toBe('succeeded')
    expect(result.rounds).toBe(2)
    expect(h.prompts[1]).toContain('SC2086')
  })

  it('执行失败 → 下一轮提示词带退出码与 stderr', async () => {
    const failing = { script: '#!/usr/bin/env bash\n# @@TU:BODY@@\nexit 3\n', notes: '', assumptions: [] }
    const h = makeHarness({
      rounds: [failing, goodScript],
      executeFor: (s) => ({
        exitCode: s.includes('exit 3') ? 3 : 0, signal: null, timedOut: false, cancelled: false,
        durationMs: 5, stdout: '', stderr: s.includes('exit 3') ? '缺少输入文件\n' : '',
      }),
    })
    const result = await runLoop({ plan: '方案', template, values: {}, runDir: '/tmp/run', config, ports: h.ports })
    expect(result.outcome).toBe('succeeded')
    expect(h.prompts[1]).toContain('退出码 3')
    expect(h.prompts[1]).toContain('缺少输入文件')
  })

  it('第 1 轮生成抛错（如 StructuredOutputError）→ 错误原文回灌，第 2 轮成功', async () => {
    const h = makeHarness({ rounds: [goodScript] })
    let calls = 0
    h.ports.opencode.generate = async (_id, message) => {
      calls += 1
      h.prompts.push(message)
      if (calls === 1) throw new Error('StructuredOutputError: 模型未按 schema 返回')
      return goodScript
    }
    const result = await runLoop({ plan: '方案', template, values: {}, runDir: '/tmp/run', config, ports: h.ports })
    expect(result.outcome).toBe('succeeded')
    expect(result.rounds).toBe(2)
    expect(h.prompts[1]).toContain('StructuredOutputError')
  })

  it('opencode 启动失败 → aborted_dependency，不抛异常', async () => {
    const h = makeHarness({ rounds: [goodScript] })
    h.ports.opencode.start = async () => { throw new Error('health 不通') }
    const result = await runLoop({ plan: '方案', template, values: {}, runDir: '/tmp/run', config, ports: h.ports })
    expect(result.outcome).toBe('aborted_dependency')
    expect(result.rounds).toBe(0)
  })

  it('连续 3 轮失败 → needs_human，且不再多发提示词', async () => {
    const broken = { script: '#!/usr/bin/env bash\n# @@TU:BODY@@\nls $f\n', notes: '', assumptions: [] }
    const h = makeHarness({
      rounds: [broken],
      shellcheckFor: () => [{ code: 'SC2086', line: 3, column: 4, level: 'warning', message: 'q' }],
    })
    const result = await runLoop({ plan: '方案', template, values: {}, runDir: '/tmp/run', config, ports: h.ports })
    expect(result.outcome).toBe('needs_human')
    expect(result.rounds).toBe(3)
    expect(h.prompts).toHaveLength(3)
  })

  it('用户拒绝执行 → 立即取消，不进入下一轮', async () => {
    const h = makeHarness({ rounds: [goodScript], confirm: false })
    const result = await runLoop({ plan: '方案', template, values: {}, runDir: '/tmp/run', config, ports: h.ports })
    expect(result.outcome).toBe('cancelled')
    expect(h.prompts).toHaveLength(1)
  })

  it('锚点缺失 → 契约失败并进入下一轮（不算 shellcheck）', async () => {
    const noAnchor = { script: '#!/usr/bin/env bash\necho hi\n', notes: '', assumptions: [] }
    const h = makeHarness({ rounds: [noAnchor, goodScript] })
    const result = await runLoop({ plan: '方案', template, values: {}, runDir: '/tmp/run', config, ports: h.ports })
    expect(result.outcome).toBe('succeeded')
    expect(h.prompts[1]).toContain('@@TU:BODY@@')
  })

  it('info 级发现不阻断（阻断级别为 warning）', async () => {
    const h = makeHarness({
      rounds: [goodScript],
      shellcheckFor: () => [{ code: 'SC2006', line: 1, column: 1, level: 'info', message: 'use $()' }],
    })
    const result = await runLoop({ plan: '方案', template, values: {}, runDir: '/tmp/run', config, ports: h.ports })
    expect(result.outcome).toBe('succeeded')
  })

  it('信任模板时 confirm 不被调用', async () => {
    let called = 0
    const h = makeHarness({ rounds: [goodScript] })
    h.ports.confirm = async () => { called += 1; return true }
    await runLoop({ plan: '方案', template, values: {}, runDir: '/tmp/run', config, ports: h.ports })
    expect(called).toBe(0)
  })
})
```

- [ ] **步骤 2：运行测试验证失败**

运行：`npx vitest run src/engine/orchestrator/loop.test.ts`
预期：FAIL，`Failed to resolve import "./loop.js"`

- [ ] **步骤 3：写最小实现**

```ts
// src/engine/orchestrator/loop.ts
import type { LoopPorts } from '../ports.js'
import type { FailureEvidence, RunConfig, RunOutcome, ShellcheckFinding, ExecuteResult } from '../types.js'
import { blocksRun } from '../types.js'
import { renderTemplate, type PlaceholderSpec } from '../template-store/render.js'
import { checkContract, extractAnchors, normalizeScript } from './contract.js'
import { OUTPUT_SCHEMA, buildFirstMessage, buildRepairMessage } from './prompt.js'

export interface LoopInput {
  plan: string
  template: { id: string; body: string; anchors: string[]; trusted: boolean; placeholders: PlaceholderSpec[] }
  values: Record<string, string>
  runDir: string
  agentName?: string
  config: RunConfig
  ports: LoopPorts
  signal?: AbortSignal
}

export interface LoopResult {
  outcome: RunOutcome
  rounds: number
  scriptPath?: string
  lastFindings: ShellcheckFinding[]
  lastExecute?: ExecuteResult
}

const TAIL = 40

function tail(text: string): string {
  return text.split('\n').slice(-TAIL).join('\n')
}

/**
 * 主状态机（规格 §6）。只有这里知道流程；外部世界全部由 ports 注入，
 * 因此可在没有 opencode / 没有 Windows 的机器上完整单测。
 */
export async function runLoop(input: LoopInput): Promise<LoopResult> {
  const { ports, config } = input
  const emit = ports.emit
  const now = ports.now ?? (() => Date.now())
  const agentName = input.agentName ?? 'tu-shell-writer'

  const skeleton = renderTemplate(input.template.body, input.template.placeholders, input.values)
  const anchors = input.template.anchors.length > 0 ? input.template.anchors : extractAnchors(skeleton)

  emit({ type: 'phase', phase: 'precheck', round: 0 })
  const detection = await ports.toolchain.detect()
  if (detection.problems.length > 0) {
    emit({ type: 'note', message: detection.problems.join('\n') })
    return { outcome: 'aborted_dependency', rounds: 0, lastFindings: [] }
  }

  let startResult: { sessionId: string }
  try {
    startResult = await ports.opencode.start(input.runDir, {
      agentName,
      ...(config.model !== undefined ? { model: config.model } : {}),
    })
  } catch (error) {
    const message = `opencode 启动失败：${(error as Error).message}`
    emit({ type: 'note', message })
    await ports.store.writeAttempt(0, { 'start-error.txt': `${message}\n` })
    await ports.store.writeMeta({ outcome: 'aborted_dependency', rounds: 0 })
    return { outcome: 'aborted_dependency', rounds: 0, lastFindings: [] }
  }
  const { sessionId } = startResult

  let evidence: FailureEvidence | undefined
  let lastFindings: ShellcheckFinding[] = []
  let lastExecute: ExecuteResult | undefined

  for (let round = 1; round <= config.maxRounds; round += 1) {
    if (input.signal?.aborted === true) return { outcome: 'cancelled', rounds: round - 1, lastFindings }
    const startedAt = now()

    emit({ type: 'phase', phase: round === 1 ? 'generating' : 'repairing', round })
    const message = evidence === undefined
      ? buildFirstMessage({ skeleton, anchors, plan: input.plan, runDir: input.runDir })
      : buildRepairMessage({ evidence, anchors, skeleton })

    let generated
    try {
      generated = await ports.opencode.generate(sessionId, message, {
        schema: OUTPUT_SCHEMA,
        timeoutMs: config.generateTimeoutMs,
        onDelta: (text) => emit({ type: 'assistant_delta', round, text }),
        ...(input.signal !== undefined ? { signal: input.signal } : {}),
      })
    } catch (error) {
      // 结构化输出失败/超时：按规格 §13 计一次契约失败，并把错误原文回灌。
      const message = (error as Error).message
      evidence = { round, stage: 'contract', contract: { reason: 'empty', missingAnchors: [], message } }
      await ports.store.writeAttempt(round, { 'generation-error.txt': `${message}\n` })
      emit({ type: 'note', message: `第 ${round} 轮生成失败：${message}` })
      continue
    }

    const script = normalizeScript(generated.script)
    emit({ type: 'script', round, script })
    const scriptPath = await ports.store.writeScript(round, script)
    await ports.store.writeAttempt(round, {
      'notes.md': `${generated.notes}\n\n## 假设\n${generated.assumptions.map((a) => `- ${a}`).join('\n')}\n`,
    })

    // 契约
    emit({ type: 'phase', phase: 'checking', round })
    const contract = checkContract(script, anchors)
    if (!contract.ok) {
      evidence = {
        round, stage: 'contract',
        contract: { reason: contract.reason!, missingAnchors: contract.missingAnchors },
      }
      await ports.store.writeAttempt(round, { 'contract.json': `${JSON.stringify(contract, null, 2)}\n` })
      emit({ type: 'note', message: `第 ${round} 轮契约失败：${contract.reason}` })
      continue
    }

    // shellcheck
    const sc = await ports.toolchain.shellcheck(scriptPath)
    lastFindings = sc.findings
    await ports.store.writeAttempt(round, { 'shellcheck.json': sc.raw, 'shellcheck.txt': renderFindings(sc.findings) })
    emit({ type: 'shellcheck', round, findings: sc.findings })

    const blocking = sc.findings.filter((f) => blocksRun(f.level, config.blockingLevel))
    if (blocking.length > 0) {
      evidence = { round, stage: 'shellcheck', shellcheck: sc.findings }
      continue
    }

    // 执行确认
    emit({ type: 'phase', phase: 'confirming', round })
    const approved = input.template.trusted
      ? true
      : await ports.confirm.confirm({ round, scriptPath, script, trusted: false })
    if (!approved) {
      await ports.store.writeMeta({ outcome: 'cancelled', rounds: round })
      return { outcome: 'cancelled', rounds: round, scriptPath, lastFindings }
    }

    // 执行
    emit({ type: 'phase', phase: 'executing', round })
    const exec = await ports.toolchain.execute(scriptPath, {
      cwd: input.runDir,
      timeoutMs: config.executeTimeoutMs,
      ...(input.signal !== undefined ? { signal: input.signal } : {}),
    })
    lastExecute = exec
    await ports.store.writeAttempt(round, {
      'stdout.txt': exec.stdout, 'stderr.txt': exec.stderr,
      'execute.json': `${JSON.stringify({ exitCode: exec.exitCode, timedOut: exec.timedOut, cancelled: exec.cancelled, durationMs: exec.durationMs }, null, 2)}\n`,
    })
    emit({ type: 'execute', round, result: exec })

    if (exec.exitCode === 0 && !exec.timedOut && !exec.cancelled) {
      await ports.store.writeMeta({ outcome: 'succeeded', rounds: round, durationMs: now() - startedAt })
      emit({ type: 'phase', phase: 'settled', round })
      return { outcome: 'succeeded', rounds: round, scriptPath, lastFindings, lastExecute: exec }
    }
    if (exec.cancelled) {
      await ports.store.writeMeta({ outcome: 'cancelled', rounds: round })
      return { outcome: 'cancelled', rounds: round, scriptPath, lastFindings, lastExecute: exec }
    }
    evidence = {
      round, stage: 'execute',
      execute: {
        exitCode: exec.exitCode, timedOut: exec.timedOut,
        stdoutTail: tail(exec.stdout), stderrTail: tail(exec.stderr), durationMs: exec.durationMs,
      },
    }
  }

  await ports.store.writeMeta({ outcome: 'needs_human', rounds: config.maxRounds })
  emit({ type: 'phase', phase: 'settled', round: config.maxRounds })
  return { outcome: 'needs_human', rounds: config.maxRounds, lastFindings, ...(lastExecute !== undefined ? { lastExecute } : {}) }
}

export function renderFindings(findings: ShellcheckFinding[]): string {
  return findings.map((f) => `${f.code} ${f.line}:${f.column} ${f.level} ${f.message}`).join('\n') + '\n'
}
```

- [ ] **步骤 4：运行测试验证通过**

运行：`npx vitest run src/engine/orchestrator/loop.test.ts`
预期：PASS（10 例）

- [ ] **步骤 5：Commit**

```bash
git add src/engine/orchestrator/loop.ts src/engine/orchestrator/loop.test.ts
git commit -m "feat(orchestrator): 主状态机

生成→契约→shellcheck→确认→执行→判定；失败证据结构化回灌同一会话，
最多 maxRounds 轮；信任模板跳过确认；取消与依赖缺失各有独立终止态。"
```

---

### 任务 12：CLI 驱动与端到端验证

**文件：**
- 创建：`src/engine/cli.ts`、`test/fixtures/plan-simple.md`、`test/fixtures/template-single.tpl.sh`
- 测试：`src/engine/e2e.offline.test.ts`（离线全链路，注入假适配器）、`src/engine/e2e.live.test.ts`（真实 opencode，默认跳过）

- [ ] **步骤 1：写失败的测试**

```ts
// src/engine/e2e.offline.test.ts —— 真实 shellcheck + 真实 bash + 假 opencode
import { mkdtemp, readFile } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { describe, expect, it } from 'vitest'
import { runLoop } from './orchestrator/loop.js'
import { RunStore } from './run-store/store.js'
import { runShellcheck } from './shell-toolchain/shellcheck.js'
import { runScript } from './shell-toolchain/execute.js'
import type { LoopPorts, OpencodePort } from './ports.js'

const BASH = process.env['TU_BASH'] ?? '/usr/bin/bash'
const SHELLCHECK = process.env['TU_SHELLCHECK'] ?? 'shellcheck'

describe('离线全链路（真实 shellcheck + 真实 bash）', () => {
  it('第 1 轮 SC2086 → 第 2 轮修好并执行成功', async () => {
    const root = await mkdtemp(join(tmpdir(), 'tu-e2e-'))
    const runDir = join(root, 'r1')
    const store = new RunStore(runDir)
    await store.init()

    const broken = '#!/usr/bin/env bash\nset -euo pipefail\n# @@TU:BODY@@\nfiles="a b"\nfor f in $files; do echo $f; done\necho done\n'
    const fixed = '#!/usr/bin/env bash\nset -euo pipefail\n# @@TU:BODY@@\nfiles="a b"\nfor f in $files; do echo "$f"; done\necho done\n'
    let turn = 0

    const opencode: OpencodePort = {
      start: async () => ({ sessionId: 'ses_offline' }),
      generate: async () => {
        turn += 1
        return turn === 1
          ? { script: broken, notes: '首轮', assumptions: [] }
          : { script: fixed, notes: '补引号', assumptions: [] }
      },
      abort: async () => {},
      dispose: async () => {},
    }

    const ports: LoopPorts = {
      opencode,
      toolchain: {
        detect: async () => ({ problems: [] }),
        shellcheck: (p) => runShellcheck(SHELLCHECK, p),
        execute: (p, o) => runScript(BASH, p, o),
      },
      confirm: async () => true,
      store,
      emit: () => {},
    }

    const result = await runLoop({
      plan: '把一句话拆成单词逐行打印',
      template: { id: 'single', body: '#!/usr/bin/env bash\nset -euo pipefail\n# @@TU:BODY@@\n', anchors: ['@@TU:BODY@@'], trusted: true, placeholders: [] },
      values: {}, runDir, config: { runRoot: root, maxRounds: 3, generateTimeoutMs: 5000, executeTimeoutMs: 10_000, blockingLevel: 'warning' },
      ports,
    })

    expect(result.outcome).toBe('succeeded')
    expect(result.rounds).toBe(2)
    expect(await readFile(join(runDir, 'attempts', '1', 'shellcheck.json'), 'utf8')).toContain('SC2086')
    expect(await readFile(join(runDir, 'attempts', '2', 'stdout.txt'), 'utf8')).toContain('a')
    expect(JSON.parse(await readFile(join(runDir, 'meta.json'), 'utf8')).outcome).toBe('succeeded')
  })
})
```

```ts
// src/engine/e2e.live.test.ts —— 真实 opencode；默认跳过，设 TU_LIVE=1 才跑
import { mkdtemp } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { describe, expect, it } from 'vitest'
import { OpencodeAdapter } from './opencode-adapter/index.js'
import { OUTPUT_SCHEMA } from './orchestrator/prompt.js'
import { systemDetectDeps, detectAll } from './shell-toolchain/detect.js'

const live = process.env['TU_LIVE'] === '1'

describe.skipIf(!live)('真实 opencode 冒烟', () => {
  it('拿到结构化输出，且权限配置确实拒绝了执行', async () => {
    const report = await detectAll(systemDetectDeps())
    expect(report.opencode, report.problems.join('\n')).toBeDefined()

    const runDir = await mkdtemp(join(tmpdir(), 'tu-live-'))
    const adapter = new OpencodeAdapter({ opencodePath: report.opencode!.path, onNote: (m) => console.log(m) })
    try {
      const { sessionId } = await adapter.start(runDir, { agentName: 'tu-shell-writer' })
      const out = await adapter.generate(
        sessionId,
        '在这个骨架里实现：打印当前目录下所有 .md 文件的数量。\n骨架：# @@TU:BODY@@\n运行目录：' + runDir,
        { schema: OUTPUT_SCHEMA, timeoutMs: 180_000 },
      )
      expect(out.script).toContain('@@TU:BODY@@')
      expect(out.notes.length).toBeGreaterThan(0)
    } finally {
      await adapter.dispose()
    }
  }, 240_000)
})
```

- [ ] **步骤 2：运行测试验证失败**

运行：`npx vitest run src/engine/e2e.offline.test.ts`
预期：FAIL，`Failed to resolve import "./orchestrator/loop.js"`（若前 11 个任务已完成，则此步失败于断言层：`expected 'needs_human' to be 'succeeded'`，因为 cli.ts 与夹具尚未就位）

- [ ] **步骤 3：写最小实现**

```markdown
<!-- test/fixtures/plan-simple.md -->
# 方案：整理日志目录

## 目标
把 `./logs` 下所有 `.log` 文件按修改时间从新到旧列出，并打印总数。

## 约束
- 只用 bash 内建与常见 POSIX 工具
- 路径含空格时必须正确
- 目录不存在时以非零退出码结束并给出中文提示
```

```bash
# test/fixtures/template-single.tpl.sh
#!/usr/bin/env bash
set -euo pipefail

# @@TU:BODY@@

main() {
  :
}

main "$@"
```

```ts
// src/engine/cli.ts
#!/usr/bin/env node
import { readFile } from 'node:fs/promises'
import { join } from 'node:path'
import { OpencodeAdapter } from './opencode-adapter/index.js'
import { runLoop } from './orchestrator/loop.js'
import { RunStore } from './run-store/store.js'
import { makeRunId, runDirFor } from './run-store/layout.js'
import { systemDetectDeps, detectAll } from './shell-toolchain/detect.js'
import { runShellcheck } from './shell-toolchain/shellcheck.js'
import { runScript } from './shell-toolchain/execute.js'
import { TemplateStore } from './template-store/store.js'
import { renderTemplate } from './template-store/render.js'
import type { LoopPorts } from './ports.js'
import type { RunConfig } from './types.js'

function arg(name: string, fallback?: string): string {
  const i = process.argv.indexOf(`--${name}`)
  if (i === -1) {
    if (fallback === undefined) throw new Error(`缺少参数 --${name}`)
    return fallback
  }
  return process.argv[i + 1] ?? fallback ?? ''
}

async function main(): Promise<void> {
  const planPath = arg('plan')
  const templateId = arg('template', 'single')
  const runRoot = arg('run-root', join(process.cwd(), '.tu-runs'))
  const templatesDir = arg('templates-dir', join(process.cwd(), '.tu-templates'))
  const maxRounds = Number(arg('max-rounds', '3'))

  const report = await detectAll(systemDetectDeps())
  console.log('环境自检：', JSON.stringify(report, null, 2))
  if (report.problems.length > 0) {
    console.error('自检未通过：\n' + report.problems.join('\n'))
    process.exit(2)
  }

  const store = new TemplateStore(templatesDir)
  const metas = await store.list()
  const meta = metas.find((t) => t.id === templateId)
  if (meta === undefined) throw new Error(`模板不存在：${templateId}（可用：${metas.map((m) => m.id).join(', ')}）`)
  const body = await store.read(templateId)
  const skeleton = renderTemplate(body, meta.placeholders, {})
  const anchors = [...skeleton.matchAll(/#\s*(@@TU:[A-Z0-9_]+@@)/g)].map((m) => m[1]!)

  const runId = makeRunId()
  const runDir = runDirFor(runRoot, runId)
  const runStore = new RunStore(runDir)
  await runStore.init()

  const config: RunConfig = {
    runRoot, maxRounds, generateTimeoutMs: 300_000, executeTimeoutMs: 120_000,
    blockingLevel: 'warning',
    opencodePath: report.opencode!.path, bashPath: report.bash!.path, shellcheckPath: report.shellcheck!.path,
  }
  const adapter = new OpencodeAdapter({
    opencodePath: report.opencode!.path,
    onNote: (m) => console.log(`[opencode] ${m}`),
  })

  const ports: LoopPorts = {
    opencode: adapter,
    toolchain: {
      detect: async () => report,
      shellcheck: (p) => runShellcheck(report.shellcheck!.path, p),
      execute: (p, o) => runScript(report.bash!.path, p, o),
    },
    // CLI 模式：非交互，打印脚本后继续（trusted=false 时由 TTY 询问）
    confirm: async ({ script }) => {
      console.log('\n===== 即将执行 =====\n' + script)
      if (process.stdin.isTTY !== true) return true
      return await new Promise<boolean>((resolve) => {
        process.stdout.write('执行？[y/N] ')
        process.stdin.once('data', (d) => resolve(d.toString().trim().toLowerCase() === 'y'))
      })
    },
    store: runStore,
    emit: (event) => {
      if (event.type === 'phase') console.log(`[轮 ${event.round}] ${event.phase}`)
      if (event.type === 'assistant_delta') process.stdout.write(event.text)
      if (event.type === 'shellcheck') console.log(`\nshellcheck：${event.findings.length} 条`)
      if (event.type === 'execute') console.log(`\n执行退出码 ${String(event.result.exitCode)}（${event.result.durationMs}ms）\n${event.result.stdout}`)
      if (event.type === 'note') console.log(`\n[note] ${event.message}`)
    },
  }

  try {
    const result = await runLoop({
      plan: await readFile(planPath, 'utf8'),
      template: { id: templateId, body, anchors, trusted: false, placeholders: meta.placeholders },
      values: {}, runDir, config, ports,
    })
    console.log(`\n结论：${result.outcome}（${result.rounds} 轮）\n运行目录：${runDir}`)
    process.exitCode = result.outcome === 'succeeded' ? 0 : 1
  } finally {
    await adapter.dispose()
  }
}

await main()
```

- [ ] **步骤 4：运行验证**

先跑离线全链路（确定性，不需要 opencode）：

运行：`npx vitest run src/engine/e2e.offline.test.ts`
预期：PASS，且 `attempts/1/shellcheck.json` 含 SC2086、`attempts/2/stdout.txt` 含输出、`meta.json` 的 outcome 为 `succeeded`

再跑类型检查与全部单测：

运行：`npx tsc --noEmit && npx vitest run`
预期：tsc 无错误；全部测试 PASS

最后跑真实 opencode 冒烟（需已登录供应商）：

运行：`TU_LIVE=1 npx vitest run src/engine/e2e.live.test.ts`
预期：PASS，`out.script` 含 `@@TU:BODY@@`。若失败于"未返回 structured_output"，按任务 10 的错误提示改传 `outputFormat` 并更新该用例。

再跑一次 CLI（真实端到端）：

运行：`npx tsx src/engine/cli.ts --plan test/fixtures/plan-simple.md --template single --run-root /tmp/tu-runs`
预期：打印自检结果、每轮阶段、shellcheck 计数、执行输出，最后 `结论：succeeded`

- [ ] **步骤 5：Commit**

```bash
git add src/engine/cli.ts src/engine/e2e.offline.test.ts src/engine/e2e.live.test.ts test/fixtures/
git commit -m "feat(engine): CLI 驱动与端到端验证

离线全链路用真实 shellcheck + 真实 bash 验证'第 2 轮修好'闭环；
真实 opencode 冒烟默认跳过（TU_LIVE=1 启用）。"
```

---

## 完成标准（Plan 1）

全部满足才算 Plan 1 完成：

- [ ] `npx tsc --noEmit` 无错误；`npx vitest run` 全绿。
- [ ] 离线全链路用例证明：坏脚本第 1 轮被 shellcheck 拦下、第 2 轮修好、真实 bash 执行成功，且 `attempts/1`、`attempts/2`、`meta.json` 产物齐全。
- [ ] `TU_LIVE=1` 真实 opencode 冒烟通过，且日志里**没有** `[opencode] 已自动拒绝权限请求` 之外的非预期权限事件（出现即说明 agent 定义漏了 deny，需回去补任务 9 的键）。
- [ ] CLI 能对 `test/fixtures/plan-simple.md` 跑出 `结论：succeeded`。
- [ ] `git log` 有 12 个任务各自的 commit。

## 已知未覆盖（交给 Plan 2 与 Windows 手测）

- Electron 外壳、IPC、三区 UI、设置页、环境自检页、历史回放、打包（Plan 2）。
- Windows 专属：Git Bash 路径探测、`taskkill /T /F`、中文与含空格路径、UTF-8 输出、`serve` 在原生 Windows 的可用性、结构化输出是否真的返回、**权限 deny 是否真的覆盖全局 allow**（规格 §14 手测清单）。

## 自检记录

- **规格覆盖度**：§5 组件（除 renderer/main）→ 任务 2–11；§6 状态机 → 任务 11；§7.1–7.6 → 任务 9–11；§8 → 任务 2–3；§9 → 任务 5；§10 → 任务 4；§11 → 任务 6–7；§13 → 任务 11 的契约与依赖分支；§14 → 任务 12 + 完成标准；§12（UI）与 §17 的 M4（打包）→ Plan 2。
- **占位符扫描**：无 "TODO/待定/类似任务 N"；每个代码步骤都有可运行代码。
- **类型一致性**：`LoopPorts`/`OpencodePort`/`ToolchainPort`/`RunStorePort` 在任务 1 定义，任务 11、12 使用同一签名；`ShellcheckFinding`、`ExecuteResult`、`ContractResult`、`FailureEvidence` 全程一致；`blocksRun`/`SEVERITY_RANK` 仅在 `types.ts` 定义。
- **自检中抓到并已修复的两个真实缺陷**：
  1. 第一版 `SseParser` 无法合并被拆到多个 `data:` 行的同一帧（会丢事件）——改为"累积 data 行直到 JSON 可解析，空行丢弃坏帧"，与任务 10 的用例期望一致。
  2. `runLoop` 未捕获 `opencode.start()` 抛错，与规格 §13 要求的 `aborted_dependency` 不符——已加 try/catch + `start-error.txt` + `meta.json`，并补了对应用例；同时让"生成阶段失败"的证据带上 `message`，与规格 §13 的"计一次契约失败并回灌"对齐。
