import { Braces, Check, Clipboard, ExternalLink, FileCode2, Files, FileText, MessageSquareWarning, PackageCheck, Send } from 'lucide-react'
import { useMemo, useState } from 'react'
import type { FormEvent } from 'react'
import type { TaskResult } from '../types'
import { normalizeArtifacts, normalizePendingCalls, stringifyContent } from '../lib/normalize'
import { FileBrowser } from './FileBrowser'

interface InspectorProps {
  result?: TaskResult
  awaitingUser: boolean
  resuming: boolean
  onResume: (answer: string, toolCallId?: string) => void
}

type Tab = 'output' | 'files' | 'artifacts' | 'decision'

const OUTPUTS: Array<{ key: keyof TaskResult; label: string; icon: typeof FileText }> = [
  { key: 'plan', label: '计划卷轴', icon: FileText },
  { key: 'code', label: '代码产出', icon: FileCode2 },
  { key: 'review', label: '审查结论', icon: Braces },
  { key: 'delivery', label: '交付说明', icon: PackageCheck },
]

export function Inspector({ result, awaitingUser, resuming, onResume }: InspectorProps) {
  const [tab, setTab] = useState<Tab>(awaitingUser ? 'decision' : 'output')
  const [answer, setAnswer] = useState('')
  const [copied, setCopied] = useState<string>()
  const artifacts = useMemo(() => normalizeArtifacts(result?.artifacts), [result?.artifacts])
  const calls = useMemo(() => normalizePendingCalls(result?.pending_tool_calls), [result?.pending_tool_calls])

  const sections = OUTPUTS.filter(({ key }) => result?.[key] != null && stringifyContent(result[key]).trim())

  async function copy(key: string, value: string) {
    await navigator.clipboard.writeText(value)
    setCopied(key)
    window.setTimeout(() => setCopied(undefined), 1200)
  }

  function submit(event: FormEvent) {
    event.preventDefault()
    if (!answer.trim() || resuming) return
    onResume(answer.trim(), calls[0]?.id)
  }

  return (
    <aside className="inspector-panel" aria-label="任务详情与产出物">
      <div className="inspector-tabs" role="tablist">
        <button className={tab === 'output' ? 'is-active' : ''} onClick={() => setTab('output')} role="tab">产出</button>
        <button className={tab === 'files' ? 'is-active' : ''} onClick={() => setTab('files')} role="tab"><Files size={13} /> 生成文件</button>
        <button className={tab === 'artifacts' ? 'is-active' : ''} onClick={() => setTab('artifacts')} role="tab">归档 <span>{artifacts.length}</span></button>
        <button className={`${tab === 'decision' ? 'is-active' : ''} ${awaitingUser ? 'needs-attention' : ''}`} onClick={() => setTab('decision')} role="tab">决策{awaitingUser && <i />}</button>
      </div>

      <div className="inspector-body">
        {tab === 'files' && <FileBrowser taskId={result?.task_id ?? result?.id} />}
        {tab === 'output' && (
          <div className="output-list">
            {sections.length === 0 && <div className="panel-empty"><FileText size={22} /><strong>产出物尚未生成</strong><span>每位 Agent 完成后，内容会归档到这里。</span></div>}
            {sections.map(({ key, label, icon: Icon }) => {
              const value = stringifyContent(result?.[key])
              return (
                <details className="output-section" key={String(key)} open={key === sections[0]?.key}>
                  <summary><span><Icon size={15} />{label}</span><small>{value.length.toLocaleString()} 字符</small></summary>
                  <div className="output-content">
                    <button className="copy-button" onClick={() => copy(String(key), value)} aria-label={`复制${label}`} title={`复制${label}`}>
                      {copied === key ? <Check size={14} /> : <Clipboard size={14} />}
                    </button>
                    <pre>{value}</pre>
                  </div>
                </details>
              )
            })}
          </div>
        )}

        {tab === 'artifacts' && (
          <div className="artifact-list">
            {artifacts.length === 0 && <div className="panel-empty"><PackageCheck size={22} /><strong>暂无文件</strong><span>任务生成的 artifacts 会出现在这里。</span></div>}
            {artifacts.map((artifact) => (
              <article className="artifact-card" key={artifact.id}>
                <div className="artifact-icon"><FileCode2 size={17} /></div>
                <div><strong>{artifact.name}</strong><span>{artifact.type}</span></div>
                {artifact.url ? <a href={artifact.url} target="_blank" rel="noreferrer" aria-label={`打开${artifact.name}`}><ExternalLink size={15} /></a> : artifact.content ? <button onClick={() => copy(artifact.id, artifact.content!)} aria-label={`复制${artifact.name}`}><Clipboard size={15} /></button> : null}
                {artifact.content && <pre>{artifact.content.slice(0, 320)}</pre>}
              </article>
            ))}
          </div>
        )}

        {tab === 'decision' && (
          <div className="decision-panel">
            {!awaitingUser && <div className="panel-empty"><MessageSquareWarning size={22} /><strong>当前无需决策</strong><span>Agent 举手求助时，你可以在这里拍板。</span></div>}
            {awaitingUser && (
              <>
                <div className="decision-alert"><MessageSquareWarning size={18} /><div><strong>Agent 正在等你</strong><p>{calls[0]?.prompt ?? '任务已暂停，请提供下一步决定。'}</p></div></div>
                {calls[0] && Object.keys(calls[0].args).length > 0 && <pre className="call-args">{JSON.stringify(calls[0].args, null, 2)}</pre>}
                <form onSubmit={submit}>
                  <label htmlFor="decision-answer">你的决定</label>
                  <textarea id="decision-answer" value={answer} onChange={(event) => setAnswer(event.target.value)} rows={5} placeholder="说明选择、约束或补充信息…" disabled={resuming} />
                  <button className="resume-button" disabled={!answer.trim() || resuming}>{resuming ? '正在恢复任务…' : '提交并继续'} <Send size={15} /></button>
                </form>
              </>
            )}
          </div>
        )}
      </div>
    </aside>
  )
}
