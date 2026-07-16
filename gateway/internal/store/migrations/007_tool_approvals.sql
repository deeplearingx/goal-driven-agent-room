CREATE TABLE IF NOT EXISTS tool_approvals (
    approval_id text PRIMARY KEY,
    tenant_id text NOT NULL REFERENCES tenants(tenant_id) ON DELETE CASCADE,
    task_id text NOT NULL REFERENCES tasks(task_id) ON DELETE CASCADE,
    tool_version_id text NOT NULL,
    operation_hash text NOT NULL,
    request jsonb NOT NULL,
    status text NOT NULL CHECK (status IN ('pending','approved','denied','expired')),
    requested_by text NOT NULL,
    requested_at timestamptz NOT NULL DEFAULT now(),
    expires_at timestamptz NOT NULL,
    decided_by text,
    decided_at timestamptz,
    decision_note text,
    UNIQUE (tenant_id, operation_hash)
);
CREATE INDEX IF NOT EXISTS tool_approvals_pending_idx ON tool_approvals (tenant_id, status, expires_at);
