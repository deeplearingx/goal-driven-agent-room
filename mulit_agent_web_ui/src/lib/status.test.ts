import { describe, expect, it } from 'vitest'
import { deriveStatus } from './status'

describe('deriveStatus', () => {
  it('gives terminal server states highest priority', () => {
    expect(deriveStatus({ serverStatus: 'completed', activeRole: 'developer' })).toBe('completed')
    expect(deriveStatus({ serverStatus: 'failed', activeRole: 'delivery' })).toBe('failed')
  })

  it('reports interrupted, but terminal server states still win', () => {
    expect(deriveStatus({ interrupted: true, connection: 'recovering' })).toBe('interrupted')
    expect(deriveStatus({ interrupted: true, serverStatus: 'completed' })).toBe('completed')
  })

  it('maps active roles to workflow labels', () => {
    expect(deriveStatus({ activeRole: 'planner' })).toBe('planning')
    expect(deriveStatus({ activeRole: 'developer' })).toBe('in_progress')
    expect(deriveStatus({ activeRole: 'reviewer' })).toBe('submitted_for_review')
    expect(deriveStatus({ activeRole: 'delivery' })).toBe('delivering')
  })

  it('surfaces user decisions and revision loops', () => {
    expect(deriveStatus({ serverStatus: 'awaiting_user' })).toBe('need_user_decision')
    expect(deriveStatus({ reviewDecision: 'need_user_decision' })).toBe('need_user_decision')
    expect(deriveStatus({ reviewDecision: 'revision_required' })).toBe('revision_required')
  })

  it('reports a broken live connection before transient workflow state', () => {
    expect(deriveStatus({ connection: 'recovering', activeRole: 'developer' })).toBe('disconnected')
  })
})
