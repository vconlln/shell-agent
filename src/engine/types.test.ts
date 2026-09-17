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
