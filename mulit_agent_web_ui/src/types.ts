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

export interface RuntimeInvocation {
  invocation_id: string
  task_id: string
  runtime: string
  invocation_kind: 'model' | 'tool'
  status: 'succeeded' | 'failed'
  model_profile_version_id?: string
  input_tokens?: number
  output_tokens?: number
  estimated_cost_usd?: number
  latency_ms?: number
  occurred_at?: string
}

export interface EvalRun {
  eval_run_id: string
  suite_name: string
  suite_version: string
  target_kind: string
  target_version_id: string
  status: 'passed' | 'failed'
  score?: number
  threshold?: number
  source_ref?: string
  created_at?: string
}

export interface VersionedResource {
  name: string
  version: number
  created_at?: string
}

export interface PromptVersion extends VersionedResource {
  prompt_version_id: string
  prompt_id: string
  content_hash: string
}

export interface ModelProfileVersion extends VersionedResource {
  model_profile_version_id: string
  model_name: string
  provider_account_id?: string
}

export interface AgentVersion extends VersionedResource {
  agent_version_id: string
}

export interface ToolVersion extends VersionedResource {
  tool_version_id: string
  kind: string
}

export interface SkillVersion extends VersionedResource {
  skill_version_id: string
}

export interface ProviderAccount {
  provider_account_id: string
  provider: string
  name: string
  key_reference: string
  created_at?: string
  rotated_at?: string
}

export interface PromptRelease {
  prompt_id: string
  prompt_name: string
  environment: string
  baseline_version_id: string
  candidate_version_id?: string
  candidate_weight: number
  updated_at?: string
}

export interface ToolApproval {
  approval_id: string
  task_id: string
  tool_version_id: string
  status: string
  requested_at?: string
  expires_at?: string
}

export interface AuditEvent {
  event_id: string
  action: string
  resource_type: string
  resource_id: string
  actor_id: string
  occurred_at?: string
}

export interface WebhookSubscription {
  subscription_id: string
  name: string
  kind: string
  event_types: string[]
  endpoint_host: string
  enabled: boolean
  updated_at?: string
}
