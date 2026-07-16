import { Activity, Command, Plus, ServerCog, Square, Target, Workflow, ClipboardList } from 'lucide-react'
import type { HealthStatus, TaskMode, UiStatus } from '../types'
import { UI_STATUS_LABELS } from '../lib/status'

interface AppHeaderProps {
  health?: HealthStatus
  healthError: boolean
  uiStatus: UiStatus
  taskId?: string
  taskMode?: TaskMode
  verification?: { passed: boolean; round: number; exitCode: number | null }
  canStop?: boolean
  onStop?: () => void
  onNew: () => void
  onOperations: () => void
}

export function AppHeader({ health, healthError, uiStatus, taskId, taskMode, verification, canStop, onStop, onNew, onOperations }: AppHeaderProps) {
  const models = health ? Object.values(health.roles).filter(Boolean) : []
  return (
    <header className="app-header">
      <div className="brand-lockup">
        <div className="brand-mark" aria-hidden="true"><i /><i /><i /><i /></div>
        <div><span>AGENT</span><strong>NIGHT SHIFT</strong></div>
      </div>
      <div className="header-task-state">
        <span className={`status-beacon ui-${uiStatus}`}><i />{UI_STATUS_LABELS[uiStatus]}</span>
        {taskMode && (
          <span className={`mode-badge mode-${taskMode}`} title={taskMode === 'goal' ? '目标模式：迭代到验证通过' : '工作流模式'}>
            {taskMode === 'goal' ? <Target size={12} /> : <Workflow size={12} />}
            {taskMode === 'goal' ? '目标' : '工作流'}
          </span>
        )}
        {taskMode === 'goal' && verification && (
          <span className={`verify-chip ${verification.passed ? 'is-pass' : 'is-fail'}`} title="最新一轮目标验证结果">
            验证 第{verification.round}轮 {verification.passed ? '通过' : '未过'}
          </span>
        )}
        {taskId && <code>#{taskId.slice(0, 8)}</code>}
      </div>
      <div className="header-actions">
        <div className={`health-pill ${healthError ? 'is-down' : ''}`} title={models.join(' · ') || '等待后端健康状态'}>
          {healthError ? <ServerCog size={14} /> : <Activity size={14} />}
          <span>{healthError ? '后端离线' : health ? `${models.length || 4} 个角色就绪` : '检查服务'}</span>
        </div>
        {canStop && (
          <button className="header-stop" onClick={onStop} aria-label="停止任务" title="停止当前任务">
            <Square size={13} /> <span>停止</span>
          </button>
        )}
        <button className="header-operations" onClick={onOperations} title="查看调用与评测记录"><ClipboardList size={15} /><span>运营</span></button>
        <button className="header-new-task" onClick={onNew} aria-label="新建任务"><Plus size={15} /> <span>新建任务</span> <kbd><Command size={11} />K</kbd></button>
      </div>
    </header>
  )
}
