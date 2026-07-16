import { useEffect, useRef, useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { Archive, Building2, FileOutput, ListTree, TriangleAlert } from 'lucide-react'
import { AgentOffice } from './components/AgentOffice'
import { AppHeader } from './components/AppHeader'
import { EventTimeline } from './components/EventTimeline'
import { Inspector } from './components/Inspector'
import { SessionSidebar } from './components/SessionSidebar'
import { TaskComposer } from './components/TaskComposer'
import { OperationsDrawer } from './components/OperationsDrawer'
import { cancelTask, getHealth, getSessions, getTask, resumeTask, streamTask } from './lib/api'
import { deriveStatus } from './lib/status'
import { useTaskStore } from './store/taskStore'
import type { TaskMode, TaskSubmission } from './types'

type MobileTab = 'office' | 'timeline' | 'output' | 'sessions'

export default function App() {
  const queryClient = useQueryClient()
  const task = useTaskStore()
  const controllerRef = useRef<AbortController | undefined>(undefined)
  const lastProgressRef = useRef<number>(Date.now())
  const [composerOpen, setComposerOpen] = useState(false)
  const [mobileTab, setMobileTab] = useState<MobileTab>('office')
  const [taskMode, setTaskMode] = useState<TaskMode>('workflow')
  const [operationsOpen, setOperationsOpen] = useState(false)

  const healthQuery = useQuery({
    queryKey: ['health'],
    queryFn: ({ signal }) => getHealth(signal),
    refetchInterval: 30_000,
  })
  const sessionsQuery = useQuery({
    queryKey: ['sessions'],
    queryFn: ({ signal }) => getSessions(signal),
    refetchInterval: 10_000,
  })
  const snapshotQuery = useQuery({
    queryKey: ['task', task.taskId],
    queryFn: ({ signal }) => getTask(task.taskId!, signal),
    enabled: Boolean(task.taskId && task.connection === 'recovering'),
    refetchInterval: task.connection === 'recovering' ? 2_000 : false,
  })

  useEffect(() => {
    if (!snapshotQuery.data) return
    lastProgressRef.current = Date.now() // fresh snapshot data = the task advanced
    task.applySnapshot(snapshotQuery.data)
    if (snapshotQuery.data.status === 'completed' || snapshotQuery.data.status === 'failed' || snapshotQuery.data.status === 'awaiting_user') {
      void queryClient.invalidateQueries({ queryKey: ['sessions'] })
    }
  }, [snapshotQuery.data]) // eslint-disable-line react-hooks/exhaustive-deps

  // Safety net: if a dropped stream stays in recovery with no snapshot progress
  // for a while, the run is dead — mark it 已中断 instead of spinning forever.
  useEffect(() => {
    if (task.connection !== 'recovering') return
    lastProgressRef.current = Date.now()
    const timer = window.setInterval(() => {
      if (Date.now() - lastProgressRef.current > 30_000) {
        useTaskStore.getState().markInterrupted()
      }
    }, 3_000)
    return () => window.clearInterval(timer)
  }, [task.connection])

  useEffect(() => {
    function keyboardShortcut(event: KeyboardEvent) {
      if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === 'k') {
        event.preventDefault()
        setComposerOpen(true)
      }
    }
    window.addEventListener('keydown', keyboardShortcut)
    return () => window.removeEventListener('keydown', keyboardShortcut)
  }, [])

  useEffect(() => () => controllerRef.current?.abort(), [])

  const uiStatus = deriveStatus({
    serverStatus: task.serverStatus,
    activeRole: task.activeRole,
    lastRole: task.lastRole,
    reviewDecision: task.reviewDecision,
    connection: task.connection,
    resuming: task.resuming,
    hasStarted: task.hasStarted,
    interrupted: task.interrupted,
  })

  async function startTask(submission: TaskSubmission) {
    controllerRef.current?.abort()
    const controller = new AbortController()
    controllerRef.current = controller
    task.reset(submission.instruction)
    setTaskMode(submission.mode)
    task.setConnection('connecting')
    setComposerOpen(false)
    setMobileTab('office')

    try {
      await streamTask(submission, (event) => {
        task.applyEvent(event)
        if (event.type === 'node_start') task.setConnection('live')
        if (event.type === 'task_finished') void queryClient.invalidateQueries({ queryKey: ['sessions'] })
      }, controller.signal)

      const current = useTaskStore.getState()
      if (current.serverStatus !== 'completed' && current.serverStatus !== 'failed' && current.serverStatus !== 'awaiting_user') {
        current.setConnection(current.taskId ? 'recovering' : 'offline', '事件流提前结束')
      }
    } catch (error) {
      if (controller.signal.aborted) return
      const message = error instanceof Error ? error.message : '无法连接任务事件流'
      const current = useTaskStore.getState()
      current.setConnection(current.taskId ? 'recovering' : 'offline', message)
    }
  }

  async function selectSession(id: string, title?: string) {
    controllerRef.current?.abort()
    // Seed the instruction box from the session title — TaskResult doesn't carry
    // the original prompt, so without this a history view would show it blank.
    task.reset(title ?? '')
    task.setConnection('recovering')
    try {
      const result = await getTask(id)
      task.applySnapshot(result)
      if (!result.task_id && !result.id) task.applySnapshot({ ...result, task_id: id })
      // A history task that's still 'running' can only be one that was interrupted
      // mid-stream (this SPA streams one task at a time) — show it as 已中断 and
      // don't enter the recovery poll loop.
      if (result.status === 'running') task.markInterrupted()
      setMobileTab('office')
    } catch (error) {
      task.applySnapshot({ task_id: id })
      task.setConnection('offline', error instanceof Error ? error.message : '无法读取任务快照')
    }
  }

  async function handleResume(answer: string, toolCallId?: string) {
    if (!task.taskId) return
    controllerRef.current?.abort()
    const controller = new AbortController()
    controllerRef.current = controller
    task.setResuming(true)
    task.setConnection('connecting')
    try {
      const result = await resumeTask(task.taskId, answer, toolCallId, task.applyEvent, controller.signal)
      if (result) task.applySnapshot(result)
      const current = useTaskStore.getState()
      if (!result && current.serverStatus === 'awaiting_user') current.setConnection('recovering')
      void queryClient.invalidateQueries({ queryKey: ['sessions'] })
    } catch (error) {
      if (!controller.signal.aborted) task.setConnection('offline', error instanceof Error ? error.message : '恢复任务失败')
    } finally {
      task.setResuming(false)
    }
  }

  function handleStop() {
    const id = task.taskId
    controllerRef.current?.abort() // stop receiving + halt the reconnect loop
    if (id) void cancelTask(id) // tell the server to cancel the detached run
    task.markInterrupted()
    void queryClient.invalidateQueries({ queryKey: ['sessions'] })
  }

  const canStop =
    Boolean(task.taskId) &&
    !task.interrupted &&
    task.serverStatus !== 'completed' &&
    task.serverStatus !== 'failed' &&
    (task.connection === 'connecting' || task.connection === 'live' || task.connection === 'recovering')

  const sessions = sessionsQuery.data ?? []
  return (
    <div className="app-shell" data-mobile-tab={mobileTab}>
      <AppHeader
        health={healthQuery.data}
        healthError={healthQuery.isError}
        uiStatus={uiStatus}
        taskId={task.taskId}
        taskMode={task.hasStarted ? taskMode : undefined}
        verification={task.verification}
        canStop={canStop}
        onStop={handleStop}
        onNew={() => setComposerOpen(true)}
        onOperations={() => setOperationsOpen(true)}
      />

      {task.error && (
        <div className="global-alert" role="alert"><TriangleAlert size={16} /><span>{task.error}</span></div>
      )}

      <div className="workbench-grid">
        <SessionSidebar
          sessions={sessions}
          selectedId={task.taskId}
          loading={sessionsQuery.isLoading || sessionsQuery.isFetching}
          error={sessionsQuery.error instanceof Error ? sessionsQuery.error.message : undefined}
          onSelect={(id, title) => void selectSession(id, title)}
          onNew={() => setComposerOpen(true)}
          onRefresh={() => void sessionsQuery.refetch()}
        />

        <main className="workbench-main">
          <AgentOffice
            agents={task.agents}
            activeRole={task.activeRole}
            reviewDecision={task.reviewDecision}
            round={task.round}
            connection={task.connection}
            handoff={task.handoff}
          />
          <EventTimeline entries={task.timeline} />
        </main>

        <Inspector
          result={task.result}
          awaitingUser={task.serverStatus === 'awaiting_user' || task.reviewDecision === 'need_user_decision'}
          resuming={task.resuming}
          onResume={(answer, id) => void handleResume(answer, id)}
        />
      </div>

      <nav className="mobile-nav" aria-label="移动端工作台导航">
        <button className={mobileTab === 'office' ? 'is-active' : ''} onClick={() => setMobileTab('office')}><Building2 size={18} />工坊</button>
        <button className={mobileTab === 'timeline' ? 'is-active' : ''} onClick={() => setMobileTab('timeline')}><ListTree size={18} />记录</button>
        <button className={mobileTab === 'output' ? 'is-active' : ''} onClick={() => setMobileTab('output')}><FileOutput size={18} />产出</button>
        <button className={mobileTab === 'sessions' ? 'is-active' : ''} onClick={() => setMobileTab('sessions')}><Archive size={18} />任务</button>
      </nav>

      <TaskComposer
        open={composerOpen}
        busy={task.connection === 'connecting'}
        onClose={() => setComposerOpen(false)}
        onSubmit={(submission) => void startTask(submission)}
      />
      {operationsOpen && <OperationsDrawer onClose={() => setOperationsOpen(false)} />}
    </div>
  )
}
