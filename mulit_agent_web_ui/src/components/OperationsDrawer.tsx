import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Activity, Box, Radio, ShieldCheck, SlidersHorizontal, X } from 'lucide-react'
import { useState, type ReactNode } from 'react'
import {
  getAgents, getAuditEvents, getEvalRuns, getInvocations, getModelProfiles, getPromptReleases,
  getPromptVersions, getProviderAccounts, getSkills, getToolApprovals, getTools, getWebhookSubscriptions,
  setWebhookSubscriptionEnabled,
} from '../lib/api'

type Tab = 'runtime' | 'config' | 'governance'

function invocationUsage(input?: number, output?: number, cost?: number) {
  const tokens = input == null && output == null ? 'token —' : `${(input ?? 0) + (output ?? 0)} token`
  const price = cost == null ? '成本待配置' : `$${cost.toFixed(6)}`
  return `${tokens} · ${price}`
}

function ResourceSection({ title, children }: { title: string, children: ReactNode }) {
  return <section><h2><Box size={14} /> {title}</h2>{children}</section>
}

function ResourceRows({ loading, error, empty, children }: { loading: boolean, error: boolean, empty: boolean, children: ReactNode }) {
  if (loading) return <p className="ops-empty">读取配置快照…</p>
  if (error) return <p className="ops-empty is-error">无法读取此资源，请确认当前账号具备相应权限。</p>
  if (empty) return <p className="ops-empty">当前租户尚无记录。</p>
  return <>{children}</>
}

export function OperationsDrawer({ onClose }: { onClose: () => void }) {
  const [tab, setTab] = useState<Tab>('runtime')
  const client = useQueryClient()
  const invocations = useQuery({ queryKey: ['admin', 'invocations'], queryFn: ({ signal }) => getInvocations(signal), refetchInterval: tab === 'runtime' ? 15_000 : false, enabled: tab === 'runtime' })
  const evals = useQuery({ queryKey: ['admin', 'eval-runs'], queryFn: ({ signal }) => getEvalRuns(signal), refetchInterval: tab === 'runtime' ? 30_000 : false, enabled: tab === 'runtime' })
  const prompts = useQuery({ queryKey: ['admin', 'prompts'], queryFn: ({ signal }) => getPromptVersions(signal), enabled: tab === 'config' })
  const releases = useQuery({ queryKey: ['admin', 'prompt-releases'], queryFn: ({ signal }) => getPromptReleases(signal), enabled: tab === 'config' })
  const profiles = useQuery({ queryKey: ['admin', 'model-profiles'], queryFn: ({ signal }) => getModelProfiles(signal), enabled: tab === 'config' })
  const providers = useQuery({ queryKey: ['admin', 'provider-accounts'], queryFn: ({ signal }) => getProviderAccounts(signal), enabled: tab === 'config' })
  const agents = useQuery({ queryKey: ['admin', 'agents'], queryFn: ({ signal }) => getAgents(signal), enabled: tab === 'config' })
  const tools = useQuery({ queryKey: ['admin', 'tools'], queryFn: ({ signal }) => getTools(signal), enabled: tab === 'config' })
  const skills = useQuery({ queryKey: ['admin', 'skills'], queryFn: ({ signal }) => getSkills(signal), enabled: tab === 'config' })
  const approvals = useQuery({ queryKey: ['admin', 'tool-approvals'], queryFn: ({ signal }) => getToolApprovals(signal), enabled: tab === 'governance' })
  const audit = useQuery({ queryKey: ['audit-events'], queryFn: ({ signal }) => getAuditEvents(signal), enabled: tab === 'governance' })
  const webhooks = useQuery({ queryKey: ['admin', 'webhooks'], queryFn: ({ signal }) => getWebhookSubscriptions(signal), enabled: tab === 'governance' })
  const webhookToggle = useMutation({
    mutationFn: ({ id, enabled }: { id: string, enabled: boolean }) => setWebhookSubscriptionEnabled(id, enabled),
    onSuccess: () => client.invalidateQueries({ queryKey: ['admin', 'webhooks'] }),
  })

  return <aside className="operations-drawer" aria-label="控制台">
    <header><div><span>CONTROL PLANE</span><strong>运营控制台</strong></div><button onClick={onClose} aria-label="关闭运营控制台"><X size={17} /></button></header>
    <nav className="ops-tabs" aria-label="控制台分类">
      <button className={tab === 'runtime' ? 'is-active' : ''} onClick={() => setTab('runtime')}><Activity size={13} /> 运行</button>
      <button className={tab === 'config' ? 'is-active' : ''} onClick={() => setTab('config')}><SlidersHorizontal size={13} /> 配置</button>
      <button className={tab === 'governance' ? 'is-active' : ''} onClick={() => setTab('governance')}><ShieldCheck size={13} /> 治理</button>
    </nav>
    {tab === 'runtime' && <>
      <ResourceSection title="最近调用"><ResourceRows loading={invocations.isLoading} error={invocations.isError} empty={!invocations.data?.length}>{invocations.data?.map((item) => <div className="ops-row" key={item.invocation_id}><span className={`ops-dot ${item.status === 'failed' ? 'is-failed' : ''}`} /><div><strong>{item.invocation_kind} · {item.runtime}</strong><small>{item.model_profile_version_id || '未绑定 Profile'} · {item.latency_ms ?? '—'} ms</small><small>{invocationUsage(item.input_tokens, item.output_tokens, item.estimated_cost_usd)}</small></div></div>)}</ResourceRows></ResourceSection>
      <ResourceSection title="发布评测"><ResourceRows loading={evals.isLoading} error={evals.isError} empty={!evals.data?.length}>{evals.data?.map((item) => <div className="ops-row" key={item.eval_run_id}><span className={`ops-dot ${item.status === 'failed' ? 'is-failed' : 'is-passed'}`} /><div><strong>{item.suite_name} · {item.status === 'passed' ? '通过' : '失败'}</strong><small>{item.target_kind}:{item.target_version_id} · {item.score == null ? '无分数' : `${Math.round(item.score * 100)}%`}</small></div></div>)}</ResourceRows></ResourceSection>
    </>}
    {tab === 'config' && <>
      <ResourceSection title="不可变配置版本"><ResourceRows loading={prompts.isLoading || profiles.isLoading} error={prompts.isError || profiles.isError} empty={!prompts.data?.length && !profiles.data?.length}>{prompts.data?.map((item) => <div className="ops-row" key={item.prompt_version_id}><span className="ops-dot is-passed" /><div><strong>Prompt · {item.name} v{item.version}</strong><small>{item.content_hash.slice(0, 12)}…</small></div></div>)}{profiles.data?.map((item) => <div className="ops-row" key={item.model_profile_version_id}><span className="ops-dot" /><div><strong>Model · {item.name} v{item.version}</strong><small>{item.model_name}</small></div></div>)}</ResourceRows></ResourceSection>
      <ResourceSection title="Agent · Tool · Skill"><ResourceRows loading={agents.isLoading || tools.isLoading || skills.isLoading} error={agents.isError || tools.isError || skills.isError} empty={!agents.data?.length && !tools.data?.length && !skills.data?.length}>{agents.data?.map((item) => <div className="ops-row" key={item.agent_version_id}><span className="ops-dot" /><div><strong>Agent · {item.name} v{item.version}</strong></div></div>)}{tools.data?.map((item) => <div className="ops-row" key={item.tool_version_id}><span className="ops-dot is-passed" /><div><strong>Tool · {item.name} v{item.version}</strong><small>{item.kind}</small></div></div>)}{skills.data?.map((item) => <div className="ops-row" key={item.skill_version_id}><span className="ops-dot is-passed" /><div><strong>Skill · {item.name} v{item.version}</strong></div></div>)}</ResourceRows></ResourceSection>
      <ResourceSection title="发布与凭据边界"><ResourceRows loading={releases.isLoading || providers.isLoading} error={releases.isError || providers.isError} empty={!releases.data?.length && !providers.data?.length}>{releases.data?.map((item) => <div className="ops-row" key={`${item.prompt_id}:${item.environment}`}><span className="ops-dot is-passed" /><div><strong>{item.prompt_name} → {item.environment}</strong><small>Candidate {item.candidate_weight}%</small></div></div>)}{providers.data?.map((item) => <div className="ops-row" key={item.provider_account_id}><span className="ops-dot" /><div><strong>{item.provider} · {item.name}</strong><small>密钥仅引用：{item.key_reference}</small></div></div>)}</ResourceRows></ResourceSection>
    </>}
    {tab === 'governance' && <>
      <ResourceSection title="Webhook 订阅"><ResourceRows loading={webhooks.isLoading} error={webhooks.isError} empty={!webhooks.data?.length}>{webhooks.data?.map((item) => <div className="ops-row" key={item.subscription_id}><span className={`ops-dot ${item.enabled ? 'is-passed' : 'is-failed'}`} /><div><strong>{item.kind} · {item.name}</strong><small>{item.endpoint_host} · {item.event_types.join(', ')}</small><button className="ops-action" disabled={webhookToggle.isPending} onClick={() => webhookToggle.mutate({ id: item.subscription_id, enabled: !item.enabled })}>{item.enabled ? '暂停投递' : '恢复投递'}</button></div></div>)}</ResourceRows></ResourceSection>
      <ResourceSection title="人工审批"><ResourceRows loading={approvals.isLoading} error={approvals.isError} empty={!approvals.data?.length}>{approvals.data?.map((item) => <div className="ops-row" key={item.approval_id}><span className={`ops-dot ${item.status === 'approved' ? 'is-passed' : item.status === 'rejected' ? 'is-failed' : ''}`} /><div><strong>{item.status} · {item.tool_version_id}</strong><small>任务 {item.task_id}</small></div></div>)}</ResourceRows></ResourceSection>
      <ResourceSection title="审计留痕"><ResourceRows loading={audit.isLoading} error={audit.isError} empty={!audit.data?.length}>{audit.data?.map((item) => <div className="ops-row" key={item.event_id}><span className="ops-dot" /><div><strong>{item.action} · {item.resource_type}</strong><small>{item.actor_id} · {item.resource_id}</small></div></div>)}</ResourceRows></ResourceSection>
    </>}
    <footer className="ops-footer"><Radio size={12} /> 读取的数据按租户与 RBAC 权限隔离；配置版本不可原地修改。</footer>
  </aside>
}
