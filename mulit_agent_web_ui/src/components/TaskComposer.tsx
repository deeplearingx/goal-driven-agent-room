import { ArrowUpRight, Plus, Sparkles, Target, Trash2, Workflow, X } from 'lucide-react'
import { useEffect, useRef, useState } from 'react'
import type { FormEvent } from 'react'
import type { TaskMode, TaskSubmission } from '../types'

interface TaskComposerProps {
  open: boolean
  busy: boolean
  onClose: () => void
  onSubmit: (submission: TaskSubmission) => void
}

interface FileRow {
  path: string
  content: string
}

export function TaskComposer({ open, busy, onClose, onSubmit }: TaskComposerProps) {
  const [task, setTask] = useState('')
  const [mode, setMode] = useState<TaskMode>('workflow')
  const [verifyCommand, setVerifyCommand] = useState('')
  const [maxIterations, setMaxIterations] = useState(10)
  const [files, setFiles] = useState<FileRow[]>([{ path: '', content: '' }])
  const inputRef = useRef<HTMLTextAreaElement>(null)

  useEffect(() => {
    if (open) window.setTimeout(() => inputRef.current?.focus(), 50)
  }, [open])

  if (!open) return null

  function submit(event: FormEvent) {
    event.preventDefault()
    const value = task.trim()
    if (!value || busy) return
    if (mode === 'workflow') {
      onSubmit({ instruction: value, mode: 'workflow' })
      return
    }
    const verifyFiles: Record<string, string> = {}
    for (const row of files) {
      const path = row.path.trim()
      if (path) verifyFiles[path] = row.content
    }
    onSubmit({
      instruction: value,
      mode: 'goal',
      verifyCommand: verifyCommand.trim() || undefined,
      verifyFiles: Object.keys(verifyFiles).length ? verifyFiles : undefined,
      maxIterations,
    })
  }

  function updateFile(index: number, patch: Partial<FileRow>) {
    setFiles((rows) => rows.map((row, i) => (i === index ? { ...row, ...patch } : row)))
  }

  return (
    <div className="composer-backdrop" role="presentation" onMouseDown={(event) => event.target === event.currentTarget && onClose()}>
      <section className="task-composer" role="dialog" aria-modal="true" aria-labelledby="composer-title">
        <header>
          <div className="composer-mark"><Sparkles size={19} /></div>
          <div><span className="eyebrow">NEW MISSION</span><h2 id="composer-title">交给 Agent 团队</h2></div>
          <button className="icon-button" onClick={onClose} aria-label="关闭"><X size={18} /></button>
        </header>
        <form onSubmit={submit}>
          <div className="mode-switch" role="tablist" aria-label="运行模式">
            <button
              type="button"
              role="tab"
              aria-selected={mode === 'workflow'}
              className={`mode-tab ${mode === 'workflow' ? 'is-active' : ''}`}
              onClick={() => setMode('workflow')}
              disabled={busy}
            >
              <Workflow size={15} /> 工作流
            </button>
            <button
              type="button"
              role="tab"
              aria-selected={mode === 'goal'}
              className={`mode-tab ${mode === 'goal' ? 'is-active' : ''}`}
              onClick={() => setMode('goal')}
              disabled={busy}
            >
              <Target size={15} /> 目标模式
            </button>
          </div>
          <div className="composer-help mode-help">
            {mode === 'workflow'
              ? '固定流水线跑一遍：策划 → 开发 → 审查 → 交付。'
              : '给定验证命令，持续迭代直到命令通过（exit 0）才算达成。'}
          </div>

          <label htmlFor="task-input">任务指令</label>
          <textarea
            ref={inputRef}
            id="task-input"
            value={task}
            onChange={(event) => setTask(event.target.value)}
            placeholder="例如：分析现有登录流程，修复刷新后状态丢失，并补齐测试。"
            rows={5}
            disabled={busy}
          />

          {mode === 'goal' && (
            <div className="goal-fields">
              <label htmlFor="verify-command">验证命令</label>
              <input
                id="verify-command"
                type="text"
                value={verifyCommand}
                onChange={(event) => setVerifyCommand(event.target.value)}
                placeholder="python check.py  或  pytest -q"
                disabled={busy}
              />
              <div className="composer-help">留空则退回审查员主观判定。命令走同一 shell 白名单，运行在任务工作区。</div>

              <div className="goal-files-head">
                <label>验证文件（每轮验证前重新写入，防篡改）</label>
                <button type="button" className="ghost-button" onClick={() => setFiles((r) => [...r, { path: '', content: '' }])} disabled={busy}>
                  <Plus size={14} /> 增加文件
                </button>
              </div>
              {files.map((row, index) => (
                <div className="goal-file-row" key={index}>
                  <div className="goal-file-row-head">
                    <input
                      type="text"
                      value={row.path}
                      onChange={(event) => updateFile(index, { path: event.target.value })}
                      placeholder="check.py"
                      disabled={busy}
                    />
                    {files.length > 1 && (
                      <button type="button" className="icon-button" onClick={() => setFiles((r) => r.filter((_, i) => i !== index))} aria-label="删除文件" disabled={busy}>
                        <Trash2 size={15} />
                      </button>
                    )}
                  </div>
                  <textarea
                    value={row.content}
                    onChange={(event) => updateFile(index, { content: event.target.value })}
                    placeholder="文件内容（例如检查脚本）"
                    rows={3}
                    disabled={busy}
                  />
                </div>
              ))}

              <label htmlFor="max-iter">最大迭代轮数</label>
              <input
                id="max-iter"
                type="number"
                min={1}
                max={50}
                value={maxIterations}
                onChange={(event) => setMaxIterations(Math.max(1, Math.min(50, Number(event.target.value) || 10)))}
                disabled={busy}
              />
            </div>
          )}

          <button type="submit" className="launch-button" disabled={!task.trim() || busy}>
            {busy ? '正在召集团队…' : '启动协作'} <ArrowUpRight size={17} />
          </button>
        </form>
      </section>
    </div>
  )
}
