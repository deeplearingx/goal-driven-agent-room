import { ROLES, type AgentEvent, type Role, type StuckAction, type TaskResult } from '../types'

const STUCK_ACTIONS: readonly StuckAction[] = ['continue', 'replan', 'ask_user', 'abort']

export interface SseFrame {
  event?: string
  data: string
  id?: string
}

export async function* parseSseStream(
  stream: ReadableStream<Uint8Array>,
): AsyncGenerator<SseFrame> {
  const reader = stream.getReader()
  const decoder = new TextDecoder()
  let buffer = ''

  try {
    while (true) {
      const { done, value } = await reader.read()
      buffer += decoder.decode(value, { stream: !done }).replace(/\r\n/g, '\n')

      let boundary = buffer.indexOf('\n\n')
      while (boundary >= 0) {
        const block = buffer.slice(0, boundary)
        buffer = buffer.slice(boundary + 2)
        const frame = parseSseBlock(block)
        if (frame) yield frame
        boundary = buffer.indexOf('\n\n')
      }

      if (done) {
        const frame = parseSseBlock(buffer)
        if (frame) yield frame
        break
      }
    }
  } finally {
    reader.releaseLock()
  }
}

function parseSseBlock(block: string): SseFrame | null {
  if (!block.trim()) return null
  const data: string[] = []
  let event: string | undefined
  let id: string | undefined

  for (const line of block.split('\n')) {
    if (!line || line.startsWith(':')) continue
    const colon = line.indexOf(':')
    const field = colon >= 0 ? line.slice(0, colon) : line
    let value = colon >= 0 ? line.slice(colon + 1) : ''
    if (value.startsWith(' ')) value = value.slice(1)
    if (field === 'data') data.push(value)
    if (field === 'event') event = value
    if (field === 'id') id = value
  }

  return data.length ? { event, data: data.join('\n'), id } : null
}

function isRole(value: unknown): value is Role {
  return typeof value === 'string' && ROLES.includes(value as Role)
}

function asRecord(value: unknown): Record<string, unknown> {
  return value && typeof value === 'object' && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : {}
}

export function normalizeAgentEvent(frame: SseFrame): AgentEvent | null {
  let decoded: unknown
  try {
    decoded = JSON.parse(frame.data)
  } catch {
    return null
  }

  const envelope = asRecord(decoded)
  const payload = asRecord(envelope.payload ?? envelope.data ?? decoded)
  const rawType = frame.event ?? envelope.event ?? envelope.type ?? payload.type
  const type = typeof rawType === 'string' ? rawType : ''
  const roleValue = payload.role ?? envelope.role
  const role = isRole(roleValue) ? roleValue : undefined
  const taskIdValue = payload.task_id ?? envelope.task_id ?? payload.id
  const taskId = typeof taskIdValue === 'string' ? taskIdValue : undefined
  const base = { role, taskId, receivedAt: Date.now(), raw: decoded }

  if (type === 'node_start' && role) return { ...base, type, role }
  if (type === 'node_end' && role) {
    return { ...base, type, role, summary: asRecord(payload.summary ?? envelope.summary) }
  }
  if (type === 'token' && role) {
    const text = payload.text ?? envelope.text
    return { ...base, type, role, text: typeof text === 'string' ? text : String(text ?? '') }
  }
  if (type === 'tool_call' && role) {
    const tool = payload.tool ?? envelope.tool
    const args = payload.args ?? envelope.args
    return {
      ...base,
      type,
      role,
      tool: String(tool ?? ''),
      args: typeof args === 'string' ? args : JSON.stringify(args ?? ''),
    }
  }
  if (type === 'tool_result' && role) {
    const tool = payload.tool ?? envelope.tool
    const preview = payload.preview ?? envelope.preview
    return {
      ...base,
      type,
      role,
      tool: String(tool ?? ''),
      preview: typeof preview === 'string' ? preview : String(preview ?? ''),
    }
  }
  if (type === 'usage') {
    const inT = Number(payload.input_tokens ?? envelope.input_tokens ?? 0)
    const outT = Number(payload.output_tokens ?? envelope.output_tokens ?? 0)
    return {
      ...base,
      type,
      inputTokens: Number.isFinite(inT) ? inT : 0,
      outputTokens: Number.isFinite(outT) ? outT : 0,
    }
  }
  if (type === 'task_error') {
    const err = payload.error ?? envelope.error
    return { ...base, type, error: String(err ?? '任务执行失败') }
  }
  if (type === 'task_finished') {
    const result = asRecord(payload.result ?? envelope.result ?? payload) as TaskResult
    return { ...base, type, result }
  }
  if (type === 'verification_completed') {
    const exit = payload.exit_code ?? envelope.exit_code
    const round = Number(payload.round ?? envelope.round ?? 0)
    return {
      ...base,
      type,
      passed: Boolean(payload.passed ?? envelope.passed),
      exitCode: exit === null || exit === undefined ? null : Number(exit),
      round: Number.isFinite(round) ? round : 0,
    }
  }
  if (type === 'supervisor_decided') {
    const rawAction = payload.action ?? envelope.action
    const action = STUCK_ACTIONS.includes(rawAction as StuckAction)
      ? (rawAction as StuckAction)
      : 'continue'
    const reasoning = payload.reasoning ?? envelope.reasoning
    return {
      ...base,
      type,
      action,
      reasoning: typeof reasoning === 'string' ? reasoning : String(reasoning ?? ''),
    }
  }
  return null
}

export async function consumeSseResponse(
  response: Response,
  onEvent: (event: AgentEvent) => void,
): Promise<void> {
  if (!response.ok) throw new Error(`请求失败 (${response.status})`)
  if (!response.body) throw new Error('响应没有可读取的事件流')

  for await (const frame of parseSseStream(response.body)) {
    const event = normalizeAgentEvent(frame)
    if (event) onEvent(event)
  }
}
