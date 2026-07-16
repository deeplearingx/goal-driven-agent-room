CREATE TABLE IF NOT EXISTS eval_runs (
    eval_run_id text PRIMARY KEY,
    tenant_id text NOT NULL REFERENCES tenants(tenant_id) ON DELETE CASCADE,
    suite_name text NOT NULL,
    suite_version text NOT NULL,
    target_kind text NOT NULL CHECK (target_kind IN ('prompt','agent','runtime')),
    target_version_id text NOT NULL,
    status text NOT NULL CHECK (status IN ('passed','failed')),
    score numeric(8,5),
    threshold numeric(8,5),
    summary jsonb NOT NULL DEFAULT '{}'::jsonb,
    source_ref text NOT NULL DEFAULT '',
    created_by text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS eval_runs_target_idx ON eval_runs (tenant_id,target_kind,target_version_id,created_at DESC);
