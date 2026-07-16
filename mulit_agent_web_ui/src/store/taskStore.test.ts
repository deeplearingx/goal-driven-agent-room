import { beforeEach, describe, expect, it } from 'vitest'
import { useTaskStore } from './taskStore'

describe('task store', () => {
  beforeEach(() => useTaskStore.getState().reset('测试任务'))

  it('tracks role lifecycle and reviewer revision rounds', () => {
    const store = useTaskStore.getState()
    store.applyEvent({ type: 'node_start', role: 'reviewer', taskId: 'task-1', receivedAt: 1, raw: {} })
    expect(useTaskStore.getState().agents.reviewer.active).toBe(true)

    useTaskStore.getState().applyEvent({
      type: 'node_end',
      role: 'reviewer',
      summary: { decision: 'revision_required', round: 2 },
      receivedAt: 2,
      raw: {},
    })
    expect(useTaskStore.getState()).toMatchObject({ reviewDecision: 'revision_required', round: 2, activeRole: undefined })

    useTaskStore.getState().applyEvent({ type: 'node_start', role: 'developer', receivedAt: 3, raw: {} })
    expect(useTaskStore.getState().reviewDecision).toBeUndefined()
  })

  it('accumulates unicode token text without creating timeline noise', () => {
    const store = useTaskStore.getState()
    store.applyEvent({ type: 'token', role: 'planner', text: '计划', receivedAt: 1, raw: {} })
    store.applyEvent({ type: 'token', role: 'planner', text: '开始', receivedAt: 2, raw: {} })
    expect(useTaskStore.getState().agents.planner.tokens).toBe('计划开始')
    expect(useTaskStore.getState().timeline).toHaveLength(0)
  })

  it('tracks tool use and token usage on the developer', () => {
    const store = useTaskStore.getState()
    store.applyEvent({ type: 'tool_call', role: 'developer', tool: 'shell', args: 'pytest', receivedAt: 1, raw: {} })
    expect(useTaskStore.getState().agents.developer.tool).toBe('shell')

    useTaskStore.getState().applyEvent({ type: 'tool_result', role: 'developer', tool: 'shell', preview: '3 passed', receivedAt: 2, raw: {} })
    expect(useTaskStore.getState().agents.developer.tool).toBeUndefined()
    expect(useTaskStore.getState().agents.developer.toolPreview).toBe('3 passed')

    useTaskStore.getState().applyEvent({ type: 'usage', inputTokens: 100, outputTokens: 40, role: 'developer', receivedAt: 3, raw: {} })
    expect(useTaskStore.getState().agents.developer.inputTokens).toBe(100)
    expect(useTaskStore.getState().agents.developer.outputTokens).toBe(40)
  })

  it('rebuilds office + timeline + review from a snapshot (history replay)', () => {
    useTaskStore.getState().reset()
    useTaskStore.getState().applySnapshot({
      task_id: 'task-9',
      status: 'completed',
      review: { decision: 'approved' },
      artifacts: [
        { kind: 'plan', role: 'planner', round: 0 },
        { kind: 'code', role: 'developer', round: 1 },
        { kind: 'review', role: 'reviewer', round: 1 },
        { kind: 'delivery', role: 'delivery', round: 1 },
      ],
    })
    const s = useTaskStore.getState()
    // Office: every role that produced an artifact is marked completed (not blank).
    expect(s.agents.planner.completed).toBe(true)
    expect(s.agents.developer.completed).toBe(true)
    expect(s.agents.delivery.completed).toBe(true)
    // Timeline: an entry per artifact + a terminal "任务完成".
    expect(s.timeline.length).toBeGreaterThanOrEqual(5)
    expect(s.timeline.some((e) => e.label === '任务完成')).toBe(true)
    expect(s.reviewDecision).toBe('approved')
    expect(s.round).toBe(1)
    expect(s.serverStatus).toBe('completed')
  })

  it('records goal-mode verification into timeline + verification state (§6.16)', () => {
    useTaskStore.getState().reset('goal 任务')
    useTaskStore.getState().applyEvent({
      type: 'verification_completed', passed: false, exitCode: 1, round: 1, receivedAt: 1, raw: {},
    })
    let s = useTaskStore.getState()
    expect(s.verification).toEqual({ passed: false, round: 1, exitCode: 1 })
    expect(s.timeline.at(-1)).toMatchObject({ type: 'verification_completed', tone: 'warning' })
    expect(s.timeline.at(-1)?.label).toContain('第 1 轮')

    useTaskStore.getState().applyEvent({
      type: 'verification_completed', passed: true, exitCode: 0, round: 2, receivedAt: 2, raw: {},
    })
    s = useTaskStore.getState()
    expect(s.verification).toEqual({ passed: true, round: 2, exitCode: 0 })
    expect(s.timeline.at(-1)).toMatchObject({ type: 'verification_completed', tone: 'success' })
  })

  it('records supervisor strategy decisions into timeline (§6.16)', () => {
    useTaskStore.getState().reset('goal 任务')
    useTaskStore.getState().applyEvent({
      type: 'supervisor_decided', action: 'replan', reasoning: '同样的错误反复出现', receivedAt: 1, raw: {},
    })
    const s = useTaskStore.getState()
    expect(s.timeline.at(-1)).toMatchObject({ type: 'supervisor_decided' })
    expect(s.timeline.at(-1)?.label).toContain('重新规划')
    expect(s.timeline.at(-1)?.detail).toBe('同样的错误反复出现')
  })
})
