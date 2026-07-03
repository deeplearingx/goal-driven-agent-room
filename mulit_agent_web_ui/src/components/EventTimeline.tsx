import { Check, CircleDot, Compass, PlugZap, ScrollText, Target, TerminalSquare, TriangleAlert, Wrench } from 'lucide-react'
import type { TimelineEntry } from '../types'
import { ROLE_LABELS } from '../lib/status'

interface EventTimelineProps {
  entries: TimelineEntry[]
}

function EntryIcon({ type }: { type: TimelineEntry['type'] }) {
  if (type === 'node_start') return <TerminalSquare size={14} />
  if (type === 'node_end') return <Check size={14} />
  if (type === 'tool_call' || type === 'tool_result') return <Wrench size={14} />
  if (type === 'task_error') return <TriangleAlert size={14} />
  if (type === 'task_finished') return <ScrollText size={14} />
  if (type === 'connection') return <PlugZap size={14} />
  if (type === 'verification_completed') return <Target size={14} />
  if (type === 'supervisor_decided') return <Compass size={14} />
  return <CircleDot size={14} />
}

export function EventTimeline({ entries }: EventTimelineProps) {
  const visible = entries.filter((item) => item.type !== 'token').slice(-30).reverse()
  return (
    <section className="timeline-panel" aria-labelledby="timeline-title">
      <div className="panel-heading compact-heading">
        <div><span className="eyebrow">EVENT LOG</span><h2 id="timeline-title">协作记录</h2></div>
        <span className="event-count">{visible.length}</span>
      </div>
      <div className="timeline-list">
        {visible.length === 0 && <p className="empty-copy">事件会按发生顺序记录在这里。</p>}
        {visible.map((item) => (
          <article key={item.id} className={`timeline-entry tone-${item.tone ?? 'normal'}`}>
            <div className="timeline-icon"><EntryIcon type={item.type} /></div>
            <div>
              <strong>{item.label}</strong>
              <span>{item.role ? ROLE_LABELS[item.role] : '系统'} · {new Date(item.timestamp).toLocaleTimeString('zh-CN', { hour12: false })}</span>
              {item.detail && <p>{item.detail}</p>}
            </div>
          </article>
        ))}
      </div>
    </section>
  )
}
