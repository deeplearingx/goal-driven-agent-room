CREATE TABLE IF NOT EXISTS tenants (
    tenant_id text PRIMARY KEY,
    display_name text NOT NULL,
    status text NOT NULL CHECK (status IN ('active','suspended')) DEFAULT 'active',
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS principals (
    principal_id text PRIMARY KEY,
    tenant_id text NOT NULL REFERENCES tenants(tenant_id) ON DELETE CASCADE,
    subject text NOT NULL,
    kind text NOT NULL CHECK (kind IN ('user','service')),
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (tenant_id, subject)
);

CREATE TABLE IF NOT EXISTS role_bindings (
    tenant_id text NOT NULL REFERENCES tenants(tenant_id) ON DELETE CASCADE,
    principal_id text NOT NULL REFERENCES principals(principal_id) ON DELETE CASCADE,
    role text NOT NULL CHECK (role IN ('tenant_admin','operator','viewer','service')),
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, principal_id, role)
);
CREATE INDEX IF NOT EXISTS role_bindings_principal_idx ON role_bindings (principal_id);

CREATE TABLE IF NOT EXISTS audit_events (
    event_id text PRIMARY KEY,
    tenant_id text NOT NULL,
    actor_id text NOT NULL,
    action text NOT NULL,
    resource_type text NOT NULL,
    resource_id text NOT NULL,
    request_id text NOT NULL,
    data jsonb NOT NULL DEFAULT '{}'::jsonb,
    occurred_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS audit_events_tenant_time_idx ON audit_events (tenant_id, occurred_at DESC);
CREATE INDEX IF NOT EXISTS audit_events_resource_idx ON audit_events (tenant_id, resource_type, resource_id, occurred_at DESC);
