import { ROLES, type Artifact, type HealthStatus, type PendingToolCall, type Role, type ServerStatus, type SessionSummary, type TaskResult } from '../types'

export function asRecord(value: unknown): Record<string, unknown> {
  return value && typeof value === 'object' && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : {}
}

export function asServerStatus(value: unknown): ServerStatus | undefined {
  return value === 'running' || value === 'awaiting_user' || value === 'completed' || value === 'failed'
    ? value
    : undefined
}

export function stringifyContent(value: unknown): string {
  if (typeof value === 'string') return value
  if (value == null) return ''
  try {
    return JSON.stringify(value, null, 2)
  } catch {
    return String(value)
  }
}

export function normalizeArtifacts(value: unknown): Artifact[] {
  if (!Array.isArray(value)) return []
  return value.map((item, index) => {
    const record = asRecord(item)
    const name = record.name ?? record.filename ?? record.title ?? `产出物 ${index + 1}`
    const type = record.type ?? record.kind ?? record.mime_type ?? 'document'
    const content = record.content ?? record.text ?? record.body
    const url = record.url ?? record.href ?? record.download_url
    return {
      id: String(record.id ?? `${String(name)}-${index}`),
      name: String(name),
      type: String(type),
      content: content == null ? undefined : stringifyContent(content),
      url: typeof url === 'string' ? url : undefined,
      raw: item,
    }
  })
}

export function normalizePendingCalls(value: unknown): PendingToolCall[] {
  if (!Array.isArray(value)) return []
  return value.map((item, index) => {
    const record = asRecord(item)
    const args = asRecord(record.args ?? record.arguments ?? record.input)
    const prompt = record.prompt ?? record.question ?? record.description ?? args.question
    return {
      id: String(record.id ?? record.tool_call_id ?? record.call_id ?? `call-${index + 1}`),
      name: String(record.name ?? record.tool_name ?? '需要确认'),
      prompt: String(prompt ?? 'Agent 需要你的决定后才能继续。'),
      args,
      raw: item,
    }
  })
}

export function normalizeSessions(value: unknown): SessionSummary[] {
  const root = asRecord(value)
  const list = Array.isArray(value)
    ? value
    : Array.isArray(root.sessions)
      ? root.sessions
      : Array.isArray(root.items)
        ? root.items
        : []

  return list.map((item, index) => {
    const record = asRecord(item)
    return {
      id: String(record.id ?? record.task_id ?? record.session_id ?? `session-${index}`),
      title: String(
        record.name ?? record.title ?? record.task ?? record.instruction ?? `任务 ${index + 1}`,
      ),
      status: String(record.status ?? 'idle'),
      updatedAt: typeof (record.updated_at ?? record.updatedAt) === 'string'
        ? String(record.updated_at ?? record.updatedAt)
        : undefined,
      raw: item,
    }
  })
}

export function normalizeHealth(value: unknown): HealthStatus {
  const root = asRecord(value)
  const source = asRecord(root.roles ?? root.models ?? root)
  const roles: Partial<Record<Role, string>> = {}
  for (const role of ROLES) {
    const entry = source[role]
    const record = asRecord(entry)
    const model = typeof entry === 'string' ? entry : record.model ?? record.name
    if (typeof model === 'string') roles[role] = model
  }
  return {
    ok: root.ok !== false && root.status !== 'failed' && root.status !== 'unhealthy',
    roles,
    raw: value,
  }
}

export function normalizeTaskResult(value: unknown): TaskResult {
  const root = asRecord(value)
  const nested = asRecord(root.result)
  return (Object.keys(nested).length ? { ...root, ...nested } : root) as TaskResult
}
