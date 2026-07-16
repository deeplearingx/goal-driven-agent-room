CREATE TABLE IF NOT EXISTS tasks (
    task_id text PRIMARY KEY,
    tenant_id text NOT NULL,
    status text NOT NULL CHECK (status IN ('queued','running','awaiting_user','completed','failed','cancelled')),
    request jsonb NOT NULL,
    result jsonb,
    error text,
    idempotency_key text,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (tenant_id, idempotency_key)
);
CREATE INDEX IF NOT EXISTS tasks_tenant_created_idx ON tasks (tenant_id, created_at DESC);

CREATE TABLE IF NOT EXISTS task_events (
    task_id text NOT NULL REFERENCES tasks(task_id) ON DELETE CASCADE,
    sequence bigint NOT NULL,
    message_id text NOT NULL UNIQUE,
    tenant_id text NOT NULL,
    event_type text NOT NULL,
    data jsonb NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (task_id, sequence)
);
CREATE INDEX IF NOT EXISTS task_events_replay_idx ON task_events (tenant_id, task_id, sequence);

CREATE TABLE IF NOT EXISTS outbox (
    id bigserial PRIMARY KEY,
    message_id text NOT NULL UNIQUE,
    routing_key text NOT NULL,
    payload jsonb NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    published_at timestamptz,
    locked_until timestamptz,
    attempts integer NOT NULL DEFAULT 0,
    last_error text
);
CREATE INDEX IF NOT EXISTS outbox_pending_idx ON outbox (id) WHERE published_at IS NULL;

CREATE TABLE IF NOT EXISTS worker_inbox (
    message_id text PRIMARY KEY,
    task_id text NOT NULL,
    status text NOT NULL,
    attempts integer NOT NULL DEFAULT 1,
    lease_until timestamptz NOT NULL,
    updated_at timestamptz NOT NULL DEFAULT now()
);
