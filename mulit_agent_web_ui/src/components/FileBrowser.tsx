import { useEffect, useMemo, useState } from 'react'
import hljs from 'highlight.js/lib/core'
import python from 'highlight.js/lib/languages/python'
import javascript from 'highlight.js/lib/languages/javascript'
import typescript from 'highlight.js/lib/languages/typescript'
import xml from 'highlight.js/lib/languages/xml'
import css from 'highlight.js/lib/languages/css'
import json from 'highlight.js/lib/languages/json'
import markdown from 'highlight.js/lib/languages/markdown'
import bash from 'highlight.js/lib/languages/bash'
import yaml from 'highlight.js/lib/languages/yaml'
import 'highlight.js/styles/github-dark.css'

for (const [name, lang] of Object.entries({ python, javascript, typescript, xml, css, json, markdown, bash, yaml })) {
  hljs.registerLanguage(name, lang)
}
import { Eye, FileCode2, FileText, FolderOpen, Code2, RefreshCw } from 'lucide-react'
import { getWorkspaceFile, getWorkspaceFiles, workspacePreviewUrl } from '../lib/api'
import type { WorkspaceFile } from '../types'

const EXT_LANG: Record<string, string> = {
  py: 'python', js: 'javascript', jsx: 'javascript', ts: 'typescript', tsx: 'typescript',
  html: 'xml', htm: 'xml', css: 'css', json: 'json', md: 'markdown', sh: 'bash', yml: 'yaml', yaml: 'yaml',
}

function ext(path: string): string {
  return path.split('.').pop()?.toLowerCase() ?? ''
}

function isHtml(path: string): boolean {
  return ext(path) === 'html' || ext(path) === 'htm'
}

function highlight(path: string, code: string): string {
  const lang = EXT_LANG[ext(path)]
  if (lang && hljs.getLanguage(lang)) return hljs.highlight(code, { language: lang }).value
  return hljs.highlightAuto(code).value
}

function formatSize(n: number): string {
  return n > 1023 ? `${(n / 1024).toFixed(1)} KB` : `${n} B`
}

interface FileBrowserProps {
  // The current task's id — scopes the listing to workspace/<taskId> and
  // doubles as the refetch trigger when it changes.
  taskId?: string
}

export function FileBrowser({ taskId }: FileBrowserProps) {
  const [files, setFiles] = useState<WorkspaceFile[]>([])
  const [selected, setSelected] = useState<string>()
  const [content, setContent] = useState<string>('')
  const [binary, setBinary] = useState(false)
  const [mode, setMode] = useState<'code' | 'preview'>('code')
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string>()

  async function loadList() {
    setLoading(true)
    setError(undefined)
    try {
      const list = await getWorkspaceFiles(taskId)
      setFiles(list)
      if (list.length && !list.some((f) => f.path === selected)) {
        void openFile(list[0].path)
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : '无法读取文件列表')
    } finally {
      setLoading(false)
    }
  }

  async function openFile(path: string) {
    setSelected(path)
    setMode(isHtml(path) ? 'preview' : 'code')
    try {
      const file = await getWorkspaceFile(path, taskId)
      setContent(file.content)
      setBinary(file.binary)
    } catch (e) {
      setContent('')
      setError(e instanceof Error ? e.message : '无法读取文件')
    }
  }

  useEffect(() => {
    void loadList()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [taskId])

  const highlighted = useMemo(
    () => (selected && !binary ? highlight(selected, content) : ''),
    [selected, content, binary],
  )

  return (
    <div className="file-browser">
      <div className="file-browser-list">
        <div className="file-browser-head">
          <span><FolderOpen size={14} /> 生成的文件 <small>{files.length}</small></span>
          <button className="icon-button" onClick={() => void loadList()} title="刷新文件列表" aria-label="刷新文件列表">
            <RefreshCw size={13} className={loading ? 'spin' : ''} />
          </button>
        </div>
        {error && <p className="sidebar-message is-error">{error}</p>}
        {!error && files.length === 0 && (
          <p className="sidebar-message">工作区还没有文件。<br />跑一个代码生成任务试试。</p>
        )}
        {files.map((f) => (
          <button
            key={f.path}
            className={`file-row ${selected === f.path ? 'is-selected' : ''}`}
            onClick={() => void openFile(f.path)}
          >
            {isHtml(f.path) ? <FileText size={13} /> : <FileCode2 size={13} />}
            <span className="file-name">{f.path}</span>
            <small>{formatSize(f.size)}</small>
          </button>
        ))}
      </div>

      <div className="file-browser-view">
        {!selected && <div className="panel-empty"><FileCode2 size={22} /><strong>选一个文件查看</strong></div>}
        {selected && (
          <>
            <div className="file-view-head">
              <strong>{selected}</strong>
              {isHtml(selected) && (
                <div className="view-toggle">
                  <button className={mode === 'code' ? 'is-active' : ''} onClick={() => setMode('code')}>
                    <Code2 size={13} /> 源码
                  </button>
                  <button className={mode === 'preview' ? 'is-active' : ''} onClick={() => setMode('preview')}>
                    <Eye size={13} /> 预览
                  </button>
                </div>
              )}
            </div>
            {binary ? (
              <div className="panel-empty"><FileCode2 size={22} /><strong>二进制文件，无法显示</strong></div>
            ) : isHtml(selected) && mode === 'preview' ? (
              <iframe
                className="file-preview-frame"
                title={`预览 ${selected}`}
                src={workspacePreviewUrl(selected, taskId)}
                sandbox="allow-scripts"
              />
            ) : (
              <pre className="file-code hljs">
                <code dangerouslySetInnerHTML={{ __html: highlighted }} />
              </pre>
            )}
          </>
        )}
      </div>
    </div>
  )
}
