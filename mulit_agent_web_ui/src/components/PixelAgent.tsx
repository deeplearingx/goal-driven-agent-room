import type { Role } from '../types'
import { ROLE_LABELS } from '../lib/status'

const SPRITE = [
  '000111100000',
  '001111110000',
  '001222210000',
  '012232321000',
  '012222221000',
  '001233210000',
  '000111100000',
  '004444440000',
  '044444444000',
  '044544454000',
  '004444440000',
  '004600460000',
  '006600660000',
  '006600660000',
  '007700770000',
  '077000077000',
]

const TOOL_LABELS: Record<string, string> = {
  shell: '跑命令',
  read_text: '读文件',
  write_text: '写文件',
  glob: '找文件',
}

function formatTokens(n: number): string {
  return n > 999 ? `${(n / 1000).toFixed(1)}k` : String(n)
}

interface PixelAgentProps {
  role: Role
  active: boolean
  completed: boolean
  tokens: string
  decision?: string
  tool?: string
  inputTokens?: number
  outputTokens?: number
}

export function PixelAgent({
  role,
  active,
  completed,
  tokens,
  decision,
  tool,
  inputTokens = 0,
  outputTokens = 0,
}: PixelAgentProps) {
  const bubbleText = tokens.trim().slice(-160)
  const totalTokens = inputTokens + outputTokens
  // 5k tokens fills the bar; the developer's tool loop is the heaviest spender.
  const energyPct = Math.min(100, Math.round((totalTokens / 5000) * 100))
  return (
    <div
      className={`pixel-agent role-${role} ${active ? 'is-working' : ''} ${completed ? 'is-complete' : ''} ${tool ? 'is-tooling' : ''}`}
      aria-label={`${ROLE_LABELS[role]}${active ? (tool ? `正在${TOOL_LABELS[tool] ?? tool}` : '正在工作') : completed ? '已完成' : '待命'}`}
    >
      {active && tool ? (
        <div className="tool-badge" aria-hidden="true">
          <span className="tool-screen"><i /><i /><i /></span>
          <span className="tool-name">⌨ {TOOL_LABELS[tool] ?? tool}</span>
        </div>
      ) : active ? (
        <div className="thought-bubble" aria-hidden="true">
          <span>{bubbleText || '···'}</span>
          <i />
        </div>
      ) : null}
      {role === 'reviewer' && decision && !active && (
        <div className={`review-stamp stamp-${decision}`} aria-label={`审查结果：${decision}`}>
          {decision === 'revision_required' ? '退' : decision === 'need_user_decision' ? '?' : '准'}
        </div>
      )}
      <div className="sprite-shadow" />
      <div className="pixel-sprite" aria-hidden="true">
        {SPRITE.flatMap((row, y) =>
          [...row].map((color, x) => (
            <i key={`${x}-${y}`} className={`pixel pixel-${color}`} />
          )),
        )}
      </div>
      {completed && <span className="agent-check" aria-hidden="true">✓</span>}
      {totalTokens > 0 && (
        <div className="energy-bar" title={`输入 ${inputTokens} · 输出 ${outputTokens} tokens`}>
          <span className="energy-fill" style={{ width: `${energyPct}%` }} />
          <em>{formatTokens(totalTokens)}</em>
        </div>
      )}
    </div>
  )
}
