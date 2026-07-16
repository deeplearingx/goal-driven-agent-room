import type {
  AgentEvent,
  HealthStatus,
  SessionSummary,
  TaskResult,
  TaskSubmission,
  WorkspaceFile,
  WorkspaceFileContent,
  RuntimeInvocation,
  EvalRun,
  AgentVersion,
  AuditEvent,
  ModelProfileVersion,
  PromptRelease,
  PromptVersion,
  ProviderAccount,
  SkillVersion,
  ToolApproval,
  ToolVersion,
  WebhookSubscription,
} from '../types'
import { normalizeAgentEvent, parseSseStream } from './sse'
import { normalizeHealth, normalizeSessions, normalizeTaskResult } from './normalize'

const configuredBase = (import.meta.env.VITE_API_BASE_URL as string | undefined)?.trim() ?? ''
const API_BASE = configuredBase.replace(/\/$/, '')
const configuredApiKey = (import.meta.env.VITE_GATEWAY_API_KEY as string | undefined)?.trim() ?? ''
const configuredTenant = (import.meta.env.VITE_TENANT_ID as string | undefined)?.trim() ?? ''

function apiUrl(path: string): string {
  return `${API_BASE}${path}`
}

function requestHeaders(headers: HeadersInit = {}): Headers {
  const merged = new Headers(headers)
  if (configuredApiKey) merged.set('Authorization', `Bearer ${configuredApiKey}`)
  if (configuredTenant) merged.set('X-Tenant-ID', configuredTenant)
  return merged
}

async function errorFromResponse(response: Response): Promise<Error> {
  const fallback = `请求失败 (${response.status})`
  try {
    const body = await response.json() as Record<string, unknown>
    return new Error(String(body.detail ?? body.message ?? body.error ?? fallback))
  } catch {
    return new Error(fallback)
  }
}

async function readSse(
  response: Response,
  onEvent: (event: AgentEvent) => void,
  onFrameId?: (id: number) => void,
): Promise<void> {
  if (!response.ok) throw await errorFromResponse(response)
  if (!response.body) throw new Error('服务端没有返回事件流')
  for await (const frame of parseSseStream(response.body)) {
    if (onFrameId && frame.id != null && frame.id !== '') {
      const n = Number(frame.id)
      if (!Number.isNaN(n)) onFrameId(n)
    }
    const event = normalizeAgentEvent(frame)
    if (event) onEvent(event)
  }
}

const MAX_RECONNECTS = 6
const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms))

function deriveTitle(task: string): string {
  const firstLine = (task.split('\n')[0] ?? '').trim()
  if (!firstLine) return '未命名任务'
  return firstLine.length > 80 ? `${firstLine.slice(0, 77)}…` : firstLine
}

// Backend TaskRequest body from a TaskSubmission. workflow mode sends only
// title/description (first line → title, whole text → description); goal mode
// (backend §6.16) adds graph=goal + the objective-oracle fields.
export function submissionBody(submission: TaskSubmission): Record<string, unknown> {
  const body: Record<string, unknown> = {
    title: deriveTitle(submission.instruction),
    description: submission.instruction,
  }
  if (submission.mode === 'goal') {
    body.graph = 'goal'
    if (submission.verifyCommand) body.verify_command = submission.verifyCommand
    if (submission.verifyFiles && Object.keys(submission.verifyFiles).length > 0) {
      body.verify_files = submission.verifyFiles
    }
    if (submission.maxIterations) body.max_iterations = submission.maxIterations
  }
  return body
}

export async function streamTask(
  submission: TaskSubmission,
  onEvent: (event: AgentEvent) => void,
  signal?: AbortSignal,
): Promise<void> {
  // The run is detached server-side, so a dropped connection doesn't kill it —
  // we reconnect to /tasks/{id}/events?from=<lastEventId> and resume the stream.
  let lastId = -1
  let taskId: string | undefined
  let terminal = false
  const onFrameId = (id: number) => { lastId = id }
  const handle = (event: AgentEvent) => {
    if (event.taskId) taskId = event.taskId
    if (event.type === 'task_finished' || event.type === 'task_error') terminal = true
    onEvent(event)
  }

  try {
    const response = await fetch(apiUrl('/tasks/stream'), {
      method: 'POST',
      headers: requestHeaders({ 'Content-Type': 'application/json', Accept: 'text/event-stream' }),
      body: JSON.stringify(submissionBody(submission)),
      signal,
    })
    await readSse(response, handle, onFrameId)
  } catch (err) {
    if (signal?.aborted || !taskId) throw err // no task id yet → can't resume
  }

  // Reconnect loop until the run reaches a terminal event (or we give up, after
  // which the caller falls back to snapshot recovery).
  let attempt = 0
  while (!terminal && taskId && !signal?.aborted) {
    if (attempt >= MAX_RECONNECTS) throw new Error('事件流多次重连失败')
    await sleep(Math.min(400 * 2 ** attempt, 4000))
    attempt += 1
    if (signal?.aborted) return
    try {
      const r = await fetch(apiUrl(`/tasks/${encodeURIComponent(taskId)}/events?from=${lastId}`), {
        headers: requestHeaders({ Accept: 'text/event-stream' }),
        signal,
      })
      if (r.status === 409) throw new Error('任务运行已不可恢复') // → snapshot recovery
      await readSse(r, handle, onFrameId)
      attempt = 0 // progress made; reset backoff
    } catch (err) {
      if (signal?.aborted) return
      if (err instanceof Error && err.message === '任务运行已不可恢复') throw err
      // transient network error: keep retrying within the attempt budget
    }
  }
}

export async function resumeTask(
  taskId: string,
  responseText: string,
  _toolCallId: string | undefined,
  onEvent: (event: AgentEvent) => void,
  signal?: AbortSignal,
): Promise<TaskResult | undefined> {
  // Backend ResumeBody = { decision }. The user's typed answer is the decision.
  const response = await fetch(apiUrl(`/tasks/${encodeURIComponent(taskId)}/resume`), {
    method: 'POST',
    headers: requestHeaders({ 'Content-Type': 'application/json', Accept: 'text/event-stream, application/json' }),
    body: JSON.stringify({ decision: responseText }),
    signal,
  })
  if (!response.ok) throw await errorFromResponse(response)
  const contentType = response.headers.get('content-type') ?? ''
  if (contentType.includes('text/event-stream')) {
    await readSse(response, onEvent)
    return undefined
  }
  return normalizeTaskResult(await response.json())
}

export async function cancelTask(taskId: string): Promise<void> {
  // Best-effort stop: 409 means the run already finished — nothing to cancel.
  const response = await fetch(apiUrl(`/tasks/${encodeURIComponent(taskId)}/cancel`), {
    method: 'POST',
    headers: requestHeaders(),
  })
  if (!response.ok && response.status !== 409) throw await errorFromResponse(response)
}

export async function getTask(taskId: string, signal?: AbortSignal): Promise<TaskResult> {
  const response = await fetch(apiUrl(`/tasks/${encodeURIComponent(taskId)}`), { headers: requestHeaders(), signal })
  if (!response.ok) throw await errorFromResponse(response)
  return normalizeTaskResult(await response.json())
}

export async function getSessions(signal?: AbortSignal): Promise<SessionSummary[]> {
  const response = await fetch(apiUrl('/api/agent-room/sessions'), { headers: requestHeaders(), signal })
  if (!response.ok) throw await errorFromResponse(response)
  return normalizeSessions(await response.json())
}

export async function getHealth(signal?: AbortSignal): Promise<HealthStatus> {
  const response = await fetch(apiUrl('/healthz'), { headers: requestHeaders(), signal })
  if (!response.ok) throw await errorFromResponse(response)
  return normalizeHealth(await response.json())
}

export async function getInvocations(signal?: AbortSignal): Promise<RuntimeInvocation[]> {
  const response = await fetch(apiUrl('/api/v1/admin/invocations?limit=25'), { headers: requestHeaders(), signal })
  if (!response.ok) throw await errorFromResponse(response)
  const data = await response.json() as { invocations?: RuntimeInvocation[] }
  return Array.isArray(data.invocations) ? data.invocations : []
}

export async function getEvalRuns(signal?: AbortSignal): Promise<EvalRun[]> {
  const response = await fetch(apiUrl('/api/v1/admin/eval-runs?limit=25'), { headers: requestHeaders(), signal })
  if (!response.ok) throw await errorFromResponse(response)
  const data = await response.json() as { eval_runs?: EvalRun[] }
  return Array.isArray(data.eval_runs) ? data.eval_runs : []
}

async function getAdminCollection<T>(path: string, key: string, signal?: AbortSignal): Promise<T[]> {
  const response = await fetch(apiUrl(path), { headers: requestHeaders(), signal })
  if (!response.ok) throw await errorFromResponse(response)
  const data = await response.json() as Record<string, unknown>
  const items = data[key]
  return Array.isArray(items) ? items as T[] : []
}

export const getPromptVersions = (signal?: AbortSignal) => getAdminCollection<PromptVersion>('/api/v1/admin/prompts', 'prompt_versions', signal)
export const getPromptReleases = (signal?: AbortSignal) => getAdminCollection<PromptRelease>('/api/v1/admin/prompt-releases', 'prompt_releases', signal)
export const getModelProfiles = (signal?: AbortSignal) => getAdminCollection<ModelProfileVersion>('/api/v1/admin/model-profiles', 'model_profile_versions', signal)
export const getProviderAccounts = (signal?: AbortSignal) => getAdminCollection<ProviderAccount>('/api/v1/admin/provider-accounts', 'provider_accounts', signal)
export const getAgents = (signal?: AbortSignal) => getAdminCollection<AgentVersion>('/api/v1/admin/agents', 'agent_versions', signal)
export const getTools = (signal?: AbortSignal) => getAdminCollection<ToolVersion>('/api/v1/admin/tools', 'tool_versions', signal)
export const getSkills = (signal?: AbortSignal) => getAdminCollection<SkillVersion>('/api/v1/admin/skills', 'skill_versions', signal)
export const getToolApprovals = (signal?: AbortSignal) => getAdminCollection<ToolApproval>('/api/v1/admin/tool-approvals', 'tool_approvals', signal)
export const getAuditEvents = (signal?: AbortSignal) => getAdminCollection<AuditEvent>('/api/v1/audit-events?limit=50', 'events', signal)
export const getWebhookSubscriptions = (signal?: AbortSignal) => getAdminCollection<WebhookSubscription>('/api/v1/admin/webhook-subscriptions', 'subscriptions', signal)

export async function setWebhookSubscriptionEnabled(subscriptionId: string, enabled: boolean): Promise<WebhookSubscription> {
  const response = await fetch(apiUrl(`/api/v1/admin/webhook-subscriptions/${encodeURIComponent(subscriptionId)}`), {
    method: 'PUT',
    headers: requestHeaders({ 'Content-Type': 'application/json' }),
    body: JSON.stringify({ enabled }),
  })
  if (!response.ok) throw await errorFromResponse(response)
  return (await response.json()) as WebhookSubscription
}

function taskQuery(taskId?: string): string {
  return taskId ? `&task_id=${encodeURIComponent(taskId)}` : ''
}

export async function getWorkspaceFiles(taskId?: string, signal?: AbortSignal): Promise<WorkspaceFile[]> {
  const response = await fetch(apiUrl(`/workspace/files?_=1${taskQuery(taskId)}`), { headers: requestHeaders(), signal })
  if (!response.ok) throw await errorFromResponse(response)
  const data = (await response.json()) as { files?: WorkspaceFile[] }
  return Array.isArray(data.files) ? data.files : []
}

export async function getWorkspaceFile(
  path: string,
  taskId?: string,
  signal?: AbortSignal,
): Promise<WorkspaceFileContent> {
  const url = apiUrl(`/workspace/file?path=${encodeURIComponent(path)}${taskQuery(taskId)}`)
  const response = await fetch(url, { headers: requestHeaders(), signal })
  if (!response.ok) throw await errorFromResponse(response)
  return (await response.json()) as WorkspaceFileContent
}

// Same-origin URL for the sandboxed preview iframe (generated HTML + assets).
// Per-task files live under /workspace/preview/<task_id>/... (static mount).
export function workspacePreviewUrl(path: string, taskId?: string): string {
  const encoded = path.split('/').map(encodeURIComponent).join('/')
  const prefix = taskId ? `${encodeURIComponent(taskId)}/` : ''
  return apiUrl(`/workspace/preview/${prefix}${encoded}`)
}
