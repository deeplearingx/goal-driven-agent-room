import type { Role, ServerStatus, UiStatus } from '../types'

export interface StatusInput {
  serverStatus?: ServerStatus
  activeRole?: Role
  lastRole?: Role
  reviewDecision?: string
  connection?: 'idle' | 'connecting' | 'live' | 'recovering' | 'offline'
  resuming?: boolean
  hasStarted?: boolean
  interrupted?: boolean
}

export function deriveStatus(input: StatusInput): UiStatus {
  if (input.serverStatus === 'failed') return 'failed'
  if (input.serverStatus === 'completed') return 'completed'
  if (input.interrupted) return 'interrupted'
  if (input.resuming) return 'resuming'
  if (input.serverStatus === 'awaiting_user' || input.reviewDecision === 'need_user_decision') {
    return 'need_user_decision'
  }
  if (input.connection === 'offline' || input.connection === 'recovering') return 'disconnected'
  if (input.reviewDecision === 'revision_required') return 'revision_required'
  if (input.activeRole === 'planner') return 'planning'
  if (input.activeRole === 'developer') return 'in_progress'
  if (input.activeRole === 'reviewer') return 'submitted_for_review'
  if (input.activeRole === 'delivery') return 'delivering'
  if (input.reviewDecision === 'approved' || input.reviewDecision === 'approve') return 'approved'
  if (input.lastRole === 'developer') return 'submitted_for_review'
  if (input.hasStarted) return 'in_progress'
  return 'planned'
}

export const UI_STATUS_LABELS: Record<UiStatus, string> = {
  planned: '等待开工',
  planning: '正在规划',
  in_progress: '开发进行中',
  submitted_for_review: '等待审查',
  revision_required: '需要修订',
  approved: '审查通过',
  delivering: '正在交付',
  need_user_decision: '等你拍板',
  resuming: '正在恢复',
  completed: '已完成',
  failed: '执行失败',
  disconnected: '连接中断',
  interrupted: '已中断',
}

export const ROLE_LABELS: Record<Role, string> = {
  planner: '策划师',
  developer: '开发者',
  reviewer: '审查员',
  delivery: '交付官',
}
