import { create } from 'zustand'
import type { AgentEvent, AgentRuntime, Role, ServerStatus, TaskResult, TimelineEntry } from '../types'
import { ROLES } from '../types'
import { asRecord, asServerStatus } from '../lib/normalize'
import { ROLE_LABELS } from '../lib/status'

type ConnectionState = 'idle' | 'connecting' | 'live' | 'recovering' | 'offline'

interface TaskState {
  taskId?: string
  instruction: string
  serverStatus?: ServerStatus
  activeRole?: Role
  lastRole?: Role
  reviewDecision?: string
  round: number
  hasStarted: boolean
  resuming: boolean
  connection: ConnectionState
  agents: Record<Role, AgentRuntime>
  timeline: TimelineEntry[]
  result?: TaskResult
  error?: string
  interrupted?: boolean
  // Goal mode (backend §6.16): latest oracle outcome, for a header/status badge.
  verification?: { passed: boolean; round: number; exitCode: number | null }
  handoff?: { from?: Role; to: Role; nonce: number }
  reset: (instruction?: string) => void
  applyEvent: (event: AgentEvent) => void
  applySnapshot: (result: TaskResult) => void
  setConnection: (connection: ConnectionState, error?: string) => void
  setResuming: (resuming: boolean) => void
  markInterrupted: () => void
}

function initialAgents(): Record<Role, AgentRuntime> {
  return Object.fromEntries(
    ROLES.map((role) => [
      role,
      { active: false, completed: false, tokens: '', inputTokens: 0, outputTokens: 0 },
    ]),
  ) as Record<Role, AgentRuntime>
}

const TOOL_LABELS: Record<string, string> = {
  shell: '运行命令',
  read_text: '读取文件',
  write_text: '写入文件',
  glob: '查找文件',
}

const STUCK_ACTION_LABELS: Record<string, string> = {
  continue: '继续迭代',
  replan: '重新规划',
  ask_user: '询问用户',
  abort: '放弃',
}

function toolLabel(tool: string): string {
  return TOOL_LABELS[tool] ?? tool
}

function entry(
  type: TimelineEntry['type'],
  label: string,
  role?: Role,
  detail?: string,
  tone: TimelineEntry['tone'] = 'normal',
): TimelineEntry {
  return {
    id: `${Date.now()}-${Math.random().toString(36).slice(2)}`,
    type,
    role,
    label,
    detail,
    timestamp: Date.now(),
    tone,
  }
}

function resultStatus(result: TaskResult): ServerStatus | undefined {
  return asServerStatus(result.status)
}

const ARTIFACT_LABELS: Record<string, string> = {
  plan: '规划完成',
  code: '代码完成',
  review: '审查完成',
  delivery: '交付完成',
}

// Rebuild the office + timeline + review decision from a persisted snapshot, so
// opening a history item (or recovering after a dropped stream) shows the agents
// and event log — not a blank floor. The live stream builds a richer timeline;
// this is the coarser from-artifacts summary used when replaying.
function rebuildFromSnapshot(result: TaskResult): {
  agents: Record<Role, AgentRuntime>
  timeline: TimelineEntry[]
  reviewDecision?: string
  round: number
} {
  const agents = initialAgents()
  const timeline: TimelineEntry[] = []
  const artifacts = Array.isArray(result.artifacts) ? result.artifacts : []
  let round = 0
  for (const raw of artifacts) {
    const a = raw as { kind?: string; role?: Role; round?: number }
    if (a.role && agents[a.role]) agents[a.role] = { ...agents[a.role], completed: true }
    if (typeof a.round === 'number') round = Math.max(round, a.round)
    timeline.push(entry('node_end', ARTIFACT_LABELS[a.kind ?? ''] ?? '产出', a.role, undefined, 'success'))
  }
  const review = result.review as { decision?: string } | undefined
  const status = resultStatus(result)
  if (status === 'completed') timeline.push(entry('task_finished', '任务完成', 'delivery', undefined, 'success'))
  else if (status === 'failed') timeline.push(entry('task_error', '任务失败', undefined, undefined, 'danger'))
  else if (status === 'awaiting_user') timeline.push(entry('node_end', '等待用户决策', 'reviewer', undefined, 'warning'))
  return { agents, timeline, reviewDecision: review?.decision, round }
}

const initialState = {
  instruction: '',
  round: 0,
  hasStarted: false,
  resuming: false,
  connection: 'idle' as ConnectionState,
  agents: initialAgents(),
  timeline: [] as TimelineEntry[],
}

export const useTaskStore = create<TaskState>((set) => ({
  ...initialState,
  reset: (instruction = '') => set({
    ...initialState,
    instruction,
    agents: initialAgents(),
    timeline: [],
    result: undefined,
    error: undefined,
    taskId: undefined,
    serverStatus: undefined,
    activeRole: undefined,
    lastRole: undefined,
    reviewDecision: undefined,
    handoff: undefined,
    interrupted: false,
    verification: undefined,
  }),
  markInterrupted: () => set({ interrupted: true, connection: 'idle' }),
  setConnection: (connection, error) => set((state) => ({
    connection,
    error,
    timeline: connection === state.connection
      ? state.timeline
      : [...state.timeline, entry(
          'connection',
          connection === 'live' ? '实时连接已建立' : connection === 'recovering' ? '正在从快照恢复' : connection === 'offline' ? '实时连接已中断' : '正在连接',
          undefined,
          error,
          connection === 'offline' ? 'danger' : 'normal',
        )].slice(-120),
  })),
  setResuming: (resuming) => set({ resuming }),
  applySnapshot: (result) => set((state) => {
    const status = resultStatus(result)
    const id = result.task_id ?? result.id ?? state.taskId
    const rebuilt = rebuildFromSnapshot(result)
    return {
      taskId: typeof id === 'string' ? id : state.taskId,
      serverStatus: status ?? state.serverStatus,
      result,
      connection: status === 'completed' || status === 'failed' || status === 'awaiting_user' ? 'idle' : state.connection,
      instruction: typeof result.task === 'string' ? result.task : state.instruction,
      error: result.error ? String(result.error) : state.error,
      hasStarted: true,
      interrupted: false,
      activeRole: undefined,
      lastRole: undefined,
      agents: rebuilt.agents,
      timeline: rebuilt.timeline,
      reviewDecision: rebuilt.reviewDecision ?? state.reviewDecision,
      round: rebuilt.round,
    }
  }),
  applyEvent: (event) => set((state) => {
    const taskId = event.taskId ?? state.taskId
    if (event.type === 'token') {
      const current = state.agents[event.role]
      return {
        taskId,
        agents: {
          ...state.agents,
          [event.role]: { ...current, tokens: (current.tokens + event.text).slice(-1200) },
        },
      }
    }

    if (event.type === 'node_start') {
      const previous = state.activeRole
      const shouldClearRevision = event.role === 'developer' && state.reviewDecision === 'revision_required'
      return {
        taskId,
        hasStarted: true,
        serverStatus: 'running',
        activeRole: event.role,
        reviewDecision: shouldClearRevision ? undefined : state.reviewDecision,
        connection: 'live',
        handoff: { from: previous ?? state.lastRole, to: event.role, nonce: Date.now() },
        agents: {
          ...state.agents,
          [event.role]: {
            ...state.agents[event.role],
            active: true,
            completed: false,
            tokens: '',
            startedAt: event.receivedAt,
          },
        },
        timeline: [...state.timeline, entry('node_start', `${ROLE_LABELS[event.role]}开始工作`, event.role)].slice(-120),
      }
    }

    if (event.type === 'node_end') {
      const decisionValue = event.role === 'reviewer' ? event.summary.decision : undefined
      const decision = typeof decisionValue === 'string' ? decisionValue : state.reviewDecision
      const roundValue = event.role === 'reviewer' ? Number(event.summary.round ?? state.round + 1) : state.round
      const detail = event.role === 'reviewer'
        ? String(decisionValue ?? '审查完成')
        : event.role === 'planner'
          ? String(event.summary.plan_preview ?? '计划已准备')
          : event.role === 'developer'
            ? `${String(event.summary.code_chars ?? 0)} 字符`
            : `${String(event.summary.delivery_chars ?? 0)} 字符`
      const tone = decision === 'revision_required' ? 'warning' : decision === 'need_user_decision' ? 'warning' : 'success'
      return {
        taskId,
        activeRole: state.activeRole === event.role ? undefined : state.activeRole,
        lastRole: event.role,
        reviewDecision: decision,
        round: Number.isFinite(roundValue) ? roundValue : state.round,
        agents: {
          ...state.agents,
          [event.role]: {
            ...state.agents[event.role],
            active: false,
            completed: true,
            summary: event.summary,
          },
        },
        timeline: [...state.timeline, entry('node_end', `${ROLE_LABELS[event.role]}完成工作`, event.role, detail, tone)].slice(-120),
      }
    }

    if (event.type === 'tool_call') {
      const current = state.agents[event.role]
      return {
        taskId,
        agents: {
          ...state.agents,
          [event.role]: { ...current, tool: event.tool, toolPreview: undefined },
        },
        timeline: [...state.timeline, entry('tool_call', `${ROLE_LABELS[event.role]} · ${toolLabel(event.tool)}`, event.role, event.args.slice(0, 120))].slice(-120),
      }
    }

    if (event.type === 'tool_result') {
      const current = state.agents[event.role]
      return {
        taskId,
        agents: {
          ...state.agents,
          [event.role]: { ...current, tool: undefined, toolPreview: event.preview },
        },
        timeline: [...state.timeline, entry('tool_result', `${toolLabel(event.tool)} 返回`, event.role, event.preview.slice(0, 120))].slice(-120),
      }
    }

    if (event.type === 'usage') {
      if (!event.role) return { taskId }
      const current = state.agents[event.role]
      return {
        taskId,
        agents: {
          ...state.agents,
          [event.role]: {
            ...current,
            inputTokens: current.inputTokens + event.inputTokens,
            outputTokens: current.outputTokens + event.outputTokens,
          },
        },
      }
    }

    if (event.type === 'task_error') {
      return {
        taskId,
        serverStatus: 'failed',
        activeRole: undefined,
        connection: 'idle',
        error: event.error,
        timeline: [...state.timeline, entry('task_error', '任务执行失败', undefined, event.error, 'danger')].slice(-120),
      }
    }

    // Goal mode (backend §6.16). These carry no agent-office role, so they
    // only push timeline entries + track the latest oracle outcome.
    if (event.type === 'verification_completed') {
      const detail = event.passed
        ? '通过'
        : `未通过${event.exitCode === null ? '' : ` (exit ${event.exitCode})`}`
      return {
        taskId,
        verification: { passed: event.passed, round: event.round, exitCode: event.exitCode },
        timeline: [...state.timeline, entry(
          'verification_completed',
          `目标验证 · 第 ${event.round} 轮`,
          undefined,
          detail,
          event.passed ? 'success' : 'warning',
        )].slice(-120),
      }
    }

    if (event.type === 'supervisor_decided') {
      const tone = event.action === 'abort' ? 'danger' : event.action === 'ask_user' ? 'warning' : 'normal'
      return {
        taskId,
        timeline: [...state.timeline, entry(
          'supervisor_decided',
          `策略决策 · ${STUCK_ACTION_LABELS[event.action]}`,
          undefined,
          event.reasoning,
          tone,
        )].slice(-120),
      }
    }

    const result = event.result
    const status = resultStatus(result) ?? (result.error ? 'failed' : 'completed')
    const resultRecord = asRecord(result)
    const finalId = event.taskId ?? (typeof resultRecord.task_id === 'string' ? resultRecord.task_id : undefined) ?? state.taskId
    return {
      taskId: finalId,
      result,
      serverStatus: status,
      activeRole: undefined,
      connection: 'idle',
      error: status === 'failed' ? String(result.error ?? '任务执行失败') : undefined,
      timeline: [...state.timeline, entry(
        'task_finished',
        status === 'failed' ? '任务执行失败' : '任务交付完成',
        undefined,
        undefined,
        status === 'failed' ? 'danger' : 'success',
      )].slice(-120),
    }
  }),
}))
