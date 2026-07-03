export const ROLES = ['planner', 'developer', 'reviewer', 'delivery'] as const
export type Role = (typeof ROLES)[number]

export type ServerStatus = 'running' | 'awaiting_user' | 'completed' | 'failed'

export type UiStatus =
  | 'planned'
  | 'planning'
  | 'in_progress'
  | 'submitted_for_review'
  | 'revision_required'
  | 'approved'
  | 'delivering'
  | 'need_user_decision'
  | 'resuming'
  | 'completed'
  | 'failed'
  | 'disconnected'
  | 'interrupted'

export interface Artifact {
  id: string
  name: string
  type: string
  content?: string
  url?: string
  raw: unknown
}

export interface PendingToolCall {
  id: string
  name: string
  prompt: string
  args: Record<string, unknown>
  raw: unknown
}

export interface TaskResult {
  id?: string
  task_id?: string
  status?: ServerStatus
  title?: string
  task?: string
  plan?: unknown
  code?: unknown
  review?: unknown
  delivery?: unknown
  artifacts?: unknown[]
  pending_tool_calls?: unknown[]
  error?: unknown
  [key: string]: unknown
}

// What the composer emits and `streamTask` sends. workflow mode carries only
// `instruction`; goal mode adds the objective-oracle fields (backend §6.16).
export type TaskMode = 'workflow' | 'goal'

export interface TaskSubmission {
  instruction: string
  mode: TaskMode
  verifyCommand?: string
  verifyFiles?: Record<string, string>
  maxIterations?: number
}

interface EventBase {
  role?: Role
  taskId?: string
  receivedAt: number
  raw: unknown
}

export interface NodeStartEvent extends EventBase {
  type: 'node_start'
  role: Role
}

export interface NodeEndEvent extends EventBase {
  type: 'node_end'
  role: Role
  summary: Record<string, unknown>
}

export interface TokenEvent extends EventBase {
  type: 'token'
  role: Role
  text: string
}

export interface ToolCallEvent extends EventBase {
  type: 'tool_call'
  role: Role
  tool: string
  args: string
}

export interface ToolResultEvent extends EventBase {
  type: 'tool_result'
  role: Role
  tool: string
  preview: string
}

export interface UsageEvent extends EventBase {
  type: 'usage'
  inputTokens: number
  outputTokens: number
}

export interface TaskFinishedEvent extends EventBase {
  type: 'task_finished'
  result: TaskResult
}

export interface TaskErrorEvent extends EventBase {
  type: 'task_error'
  error: string
}

// Goal mode (backend PLAN.md §6.16): the develop→verify loop emits one
// `verification_completed` per oracle run; `supervisor_decided` fires when
// the loop is stuck and a strategy is chosen. Both surface as timeline entries.
export interface VerificationCompletedEvent extends EventBase {
  type: 'verification_completed'
  passed: boolean
  exitCode: number | null
  round: number
}

export type StuckAction = 'continue' | 'replan' | 'ask_user' | 'abort'

export interface SupervisorDecidedEvent extends EventBase {
  type: 'supervisor_decided'
  action: StuckAction
  reasoning: string
}

export type AgentEvent =
  | NodeStartEvent
  | NodeEndEvent
  | TokenEvent
  | ToolCallEvent
  | ToolResultEvent
  | UsageEvent
  | TaskFinishedEvent
  | TaskErrorEvent
  | VerificationCompletedEvent
  | SupervisorDecidedEvent

export interface TimelineEntry {
  id: string
  type: AgentEvent['type'] | 'connection'
  role?: Role
  label: string
  detail?: string
  timestamp: number
  tone?: 'normal' | 'success' | 'warning' | 'danger'
}

export interface AgentRuntime {
  active: boolean
  completed: boolean
  tokens: string
  tool?: string
  toolPreview?: string
  inputTokens: number
  outputTokens: number
  summary?: Record<string, unknown>
  startedAt?: number
}

export interface SessionSummary {
  id: string
  title: string
  status: ServerStatus | string
  updatedAt?: string
  raw: unknown
}

export interface WorkspaceFile {
  path: string
  size: number
}

export interface WorkspaceFileContent {
  path: string
  content: string
  truncated: boolean
  binary: boolean
}

export interface HealthStatus {
  ok: boolean
  roles: Partial<Record<Role, string>>
  raw: unknown
}
