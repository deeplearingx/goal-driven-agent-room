import { describe, expect, it } from 'vitest'
import { normalizeAgentEvent, parseSseStream } from './sse'

function chunkedStream(chunks: string[]): ReadableStream<Uint8Array> {
  const encoder = new TextEncoder()
  return new ReadableStream({
    start(controller) {
      chunks.forEach((chunk) => controller.enqueue(encoder.encode(chunk)))
      controller.close()
    },
  })
}

describe('parseSseStream', () => {
  it('handles chunk boundaries, CRLF and multiline data', async () => {
    const stream = chunkedStream([
      'event: token\r\ndata: {"payload":{"role":"developer",',
      '"text":"你',
      '好"}}\r\n\r\nevent: node_end\ndata: {"payload":',
      '{"role":"developer","summary":{"code_chars":12}}}\n\n',
    ])
    const frames = []
    for await (const frame of parseSseStream(stream)) frames.push(frame)
    expect(frames).toHaveLength(2)
    expect(normalizeAgentEvent(frames[0])).toMatchObject({ type: 'token', role: 'developer', text: '你好' })
    expect(normalizeAgentEvent(frames[1])).toMatchObject({ type: 'node_end', role: 'developer', summary: { code_chars: 12 } })
  })

  it('accepts event type inside a JSON envelope', () => {
    const event = normalizeAgentEvent({
      data: JSON.stringify({ type: 'node_start', role: 'planner', task_id: 'task-1' }),
    })
    expect(event).toMatchObject({ type: 'node_start', role: 'planner', taskId: 'task-1' })
  })

  it('ignores malformed and unknown events', () => {
    expect(normalizeAgentEvent({ event: 'token', data: 'not-json' })).toBeNull()
    expect(normalizeAgentEvent({ event: 'mystery', data: '{}' })).toBeNull()
  })

  it('parses tool_call / tool_result / usage events from the backend', () => {
    const call = normalizeAgentEvent({
      event: 'tool_call',
      data: JSON.stringify({ role: 'developer', tool: 'shell', args: 'pytest -q', task_id: 't1' }),
    })
    expect(call).toMatchObject({ type: 'tool_call', role: 'developer', tool: 'shell', args: 'pytest -q' })

    const result = normalizeAgentEvent({
      event: 'tool_result',
      data: JSON.stringify({ role: 'developer', tool: 'shell', preview: '3 passed' }),
    })
    expect(result).toMatchObject({ type: 'tool_result', role: 'developer', tool: 'shell', preview: '3 passed' })

    const usage = normalizeAgentEvent({
      event: 'usage',
      data: JSON.stringify({ role: 'reviewer', input_tokens: 10, output_tokens: 5 }),
    })
    expect(usage).toMatchObject({ type: 'usage', inputTokens: 10, outputTokens: 5 })
  })

  it('parses goal-mode verification_completed / supervisor_decided (§6.16)', () => {
    const verify = normalizeAgentEvent({
      event: 'verification_completed',
      data: JSON.stringify({ passed: false, exit_code: 1, round: 2, task_id: 't1' }),
    })
    expect(verify).toMatchObject({
      type: 'verification_completed',
      passed: false,
      exitCode: 1,
      round: 2,
    })

    const pass = normalizeAgentEvent({
      event: 'verification_completed',
      data: JSON.stringify({ passed: true, exit_code: 0, round: 3 }),
    })
    expect(pass).toMatchObject({ type: 'verification_completed', passed: true, exitCode: 0, round: 3 })

    const supervisor = normalizeAgentEvent({
      event: 'supervisor_decided',
      data: JSON.stringify({ action: 'replan', reasoning: 'same error repeats' }),
    })
    expect(supervisor).toMatchObject({
      type: 'supervisor_decided',
      action: 'replan',
      reasoning: 'same error repeats',
    })
  })

  it('coerces an unknown supervisor action to continue', () => {
    const ev = normalizeAgentEvent({
      event: 'supervisor_decided',
      data: JSON.stringify({ action: 'bogus', reasoning: 'x' }),
    })
    expect(ev).toMatchObject({ type: 'supervisor_decided', action: 'continue' })
  })
})
