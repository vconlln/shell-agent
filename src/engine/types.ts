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
