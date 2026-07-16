import { describe, expect, it } from 'vitest'
import { submissionBody } from './api'

describe('submissionBody (goal mode §6.16)', () => {
  it('workflow mode sends only title/description', () => {
    const body = submissionBody({ instruction: '修复登录\n补测试', mode: 'workflow' })
    expect(body).toEqual({ title: '修复登录', description: '修复登录\n补测试' })
    expect(body.graph).toBeUndefined()
  })

  it('goal mode adds graph + verify fields', () => {
    const body = submissionBody({
      instruction: '实现 median',
      mode: 'goal',
      verifyCommand: 'python check.py',
      verifyFiles: { 'check.py': 'import sys; sys.exit(0)' },
      maxIterations: 6,
    })
    expect(body).toMatchObject({
      title: '实现 median',
      description: '实现 median',
      graph: 'goal',
      verify_command: 'python check.py',
      verify_files: { 'check.py': 'import sys; sys.exit(0)' },
      max_iterations: 6,
    })
  })

  it('goal mode omits empty verify_command / verify_files', () => {
    const body = submissionBody({ instruction: 'x', mode: 'goal', verifyFiles: {}, maxIterations: 10 })
    expect(body.graph).toBe('goal')
    expect(body.verify_command).toBeUndefined()
    expect(body.verify_files).toBeUndefined()
    expect(body.max_iterations).toBe(10)
  })
})
