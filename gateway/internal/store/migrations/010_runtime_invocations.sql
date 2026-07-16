CREATE TABLE IF NOT EXISTS runtime_invocations (
    invocation_id text PRIMARY KEY,
    tenant_id text NOT NULL REFERENCES tenants(tenant_id) ON DELETE CASCADE,
    task_id text NOT NULL REFERENCES tasks(task_id) ON DELETE CASCADE,
    runtime text NOT NULL,
    invocation_kind text NOT NULL CHECK (invocation_kind IN ('model','tool')),
    status text NOT NULL CHECK (status IN ('succeeded','failed')),
    runtime_snapshot_id text,
    prompt_version_id text,
    model_profile_version_id text,
    tool_version_id text,
    input_tokens integer CHECK (input_tokens IS NULL OR input_tokens >= 0),
    output_tokens integer CHECK (output_tokens IS NULL OR output_tokens >= 0),
    estimated_cost_usd numeric(16,8) CHECK (estimated_cost_usd IS NULL OR estimated_cost_usd >= 0),
    latency_ms integer CHECK (latency_ms IS NULL OR latency_ms >= 0),
    data jsonb NOT NULL DEFAULT '{}'::jsonb,
    occurred_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS runtime_invocations_tenant_time_idx ON runtime_invocations (tenant_id, occurred_at DESC);
CREATE INDEX IF NOT EXISTS runtime_invocations_task_idx ON runtime_invocations (tenant_id, task_id, occurred_at DESC);
