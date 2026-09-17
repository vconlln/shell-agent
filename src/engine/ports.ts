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
