import { Clock3, ListFilter, Plus, RefreshCw } from 'lucide-react'
import type { SessionSummary } from '../types'

interface SessionSidebarProps {
  sessions: SessionSummary[]
  selectedId?: string
  loading: boolean
  error?: string
  onSelect: (id: string, title: string) => void
  onNew: () => void
  onRefresh: () => void
}

const SESSION_STATUS: Record<string, string> = {
  running: '运行中',
  awaiting_user: '等你拍板',
  completed: '已完成',
  failed: '失败',
  idle: '待运行',
}

export function SessionSidebar({ sessions, selectedId, loading, error, onSelect, onNew, onRefresh }: SessionSidebarProps) {
  return (
    <aside className="session-sidebar" aria-label="任务列表">
      <div className="sidebar-title">
        <div><ListFilter size={16} /><strong>任务档案</strong></div>
        <button className="icon-button" onClick={onRefresh} aria-label="刷新任务列表" title="刷新任务列表">
          <RefreshCw size={15} className={loading ? 'spin' : ''} />
        </button>
      </div>
      <button className="new-task-button" onClick={onNew}><Plus size={16} /> 新建协作任务</button>
      <div className="session-list">
        {loading && sessions.length === 0 && <p className="sidebar-message">正在读取任务档案…</p>}
        {error && sessions.length === 0 && <p className="sidebar-message is-error">无法读取任务列表<br /><span>{error}</span></p>}
        {!loading && !error && sessions.length === 0 && <p className="sidebar-message">还没有历史任务。<br />从一条明确指令开始。</p>}
        {sessions.map((session) => (
          <button
            key={session.id}
            className={`session-item ${selectedId === session.id ? 'is-selected' : ''}`}
            onClick={() => onSelect(session.id, session.title)}
          >
            <span className={`session-dot status-${session.status}`} />
            <span className="session-copy">
              <strong>{session.title}</strong>
              <small><Clock3 size={11} /> {session.updatedAt ? new Date(session.updatedAt).toLocaleString('zh-CN', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' }) : SESSION_STATUS[session.status] ?? session.status}</small>
            </span>
          </button>
        ))}
      </div>
      <div className="sidebar-footer"><kbd>⌘</kbd><kbd>K</kbd><span>快速创建</span></div>
    </aside>
  )
}
