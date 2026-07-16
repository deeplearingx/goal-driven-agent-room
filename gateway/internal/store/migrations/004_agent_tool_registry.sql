CREATE TABLE IF NOT EXISTS agents (
    agent_id text PRIMARY KEY,
    tenant_id text NOT NULL REFERENCES tenants(tenant_id) ON DELETE CASCADE,
    name text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (tenant_id, name)
);

CREATE TABLE IF NOT EXISTS agent_versions (
    agent_version_id text PRIMARY KEY,
    agent_id text NOT NULL REFERENCES agents(agent_id) ON DELETE RESTRICT,
    tenant_id text NOT NULL,
    version integer NOT NULL CHECK (version > 0),
    specification jsonb NOT NULL,
    content_hash text NOT NULL,
    created_by text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (agent_id, version),
    UNIQUE (agent_id, content_hash)
);
CREATE INDEX IF NOT EXISTS agent_versions_tenant_idx ON agent_versions (tenant_id, created_at DESC);

CREATE TABLE IF NOT EXISTS tools (
    tool_id text PRIMARY KEY,
    tenant_id text NOT NULL REFERENCES tenants(tenant_id) ON DELETE CASCADE,
    name text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (tenant_id, name)
);

CREATE TABLE IF NOT EXISTS tool_versions (
    tool_version_id text PRIMARY KEY,
    tool_id text NOT NULL REFERENCES tools(tool_id) ON DELETE RESTRICT,
    tenant_id text NOT NULL,
    version integer NOT NULL CHECK (version > 0),
    kind text NOT NULL,
    specification jsonb NOT NULL,
    content_hash text NOT NULL,
    created_by text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (tool_id, version),
    UNIQUE (tool_id, content_hash)
);
CREATE INDEX IF NOT EXISTS tool_versions_tenant_idx ON tool_versions (tenant_id, created_at DESC);
