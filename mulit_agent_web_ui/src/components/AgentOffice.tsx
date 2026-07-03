import { Bot, Radio, RotateCcw } from 'lucide-react'
import { ROLES, type AgentRuntime, type Role } from '../types'
import { ROLE_LABELS } from '../lib/status'
import { PixelAgent } from './PixelAgent'

const ROLE_META: Record<Role, { code: string; desk: string }> = {
  planner: { code: 'PLAN', desk: '蓝图桌' },
  developer: { code: 'DEV', desk: '终端台' },
  reviewer: { code: 'REV', desk: '审查席' },
  delivery: { code: 'SHIP', desk: '打包站' },
}

interface AgentOfficeProps {
  agents: Record<Role, AgentRuntime>
  activeRole?: Role
  reviewDecision?: string
  round: number
  connection: string
  handoff?: { from?: Role; to: Role; nonce: number }
}

function rolePosition(role?: Role): string {
  const index = role ? ROLES.indexOf(role) : 0
  return `${12.5 + index * 25}%`
}

export function AgentOffice({ agents, activeRole, reviewDecision, round, connection, handoff }: AgentOfficeProps) {
  return (
    <section className="office-card" aria-labelledby="office-heading">
      <header className="panel-heading office-heading">
        <div>
          <span className="eyebrow">LIVE FLOOR · 4 AGENTS</span>
          <h2 id="office-heading">深夜协作工坊</h2>
        </div>
        <div className={`live-indicator is-${connection}`}>
          <Radio size={14} aria-hidden="true" />
          <span>{connection === 'live' ? '实时连接' : connection === 'connecting' ? '连接中' : connection === 'recovering' ? '快照恢复' : '待机'}</span>
        </div>
      </header>

      <div className="office-scene">
        <div className="office-wall" aria-hidden="true">
          <div className="pixel-window"><i /><i /><i /><i /><span /></div>
          <div className="wall-clock"><span /></div>
          <div className="shelf shelf-one"><i /><i /><i /></div>
          <div className="shelf shelf-two"><i /><i /></div>
          <div className="cable cable-one" />
        </div>

        <div className="handoff-rail" aria-label="Agent 交接轨道">
          <div className="rail-line" />
          {ROLES.map((role) => <i key={role} className="rail-stop" />)}
          {handoff && (
            <div
              key={handoff.nonce}
              className="handoff-scroll"
              style={{ '--from': rolePosition(handoff.from), '--to': rolePosition(handoff.to) } as React.CSSProperties}
              aria-label={`任务交接给${ROLE_LABELS[handoff.to]}`}
            >
              <span />
            </div>
          )}
        </div>

        <div className="agent-floor">
          {ROLES.map((role) => {
            const runtime = agents[role]
            return (
              <article key={role} className={`agent-station ${runtime.active ? 'station-active' : ''}`}>
                <div className="station-light" />
                <div className="station-screen" aria-hidden="true">
                  <span>{ROLE_META[role].code}</span>
                  <i /><i /><i />
                </div>
                <PixelAgent
                  role={role}
                  active={runtime.active}
                  completed={runtime.completed}
                  tokens={runtime.tokens}
                  decision={role === 'reviewer' ? reviewDecision : undefined}
                  tool={runtime.tool}
                  inputTokens={runtime.inputTokens}
                  outputTokens={runtime.outputTokens}
                />
                <div className="pixel-desk" aria-hidden="true"><i /><i /></div>
                <div className="station-label">
                  <strong>{ROLE_LABELS[role]}</strong>
                  <span>{runtime.active ? '工作中' : runtime.completed ? '本轮完成' : ROLE_META[role].desk}</span>
                </div>
              </article>
            )
          })}
        </div>

        {reviewDecision === 'revision_required' && !activeRole && (
          <div className="revision-banner">
            <RotateCcw size={14} /> 审查退回 · 准备第 {round + 1} 轮修订
          </div>
        )}
        {!activeRole && connection === 'idle' && !handoff && (
          <div className="floor-empty"><Bot size={16} /> 创建任务后，四位 Agent 会在这里开始协作</div>
        )}
      </div>
    </section>
  )
}
