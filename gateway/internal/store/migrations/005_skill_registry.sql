CREATE TABLE IF NOT EXISTS skills (
    skill_id text PRIMARY KEY,
    tenant_id text NOT NULL REFERENCES tenants(tenant_id) ON DELETE CASCADE,
    name text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (tenant_id, name)
);

CREATE TABLE IF NOT EXISTS skill_versions (
    skill_version_id text PRIMARY KEY,
    skill_id text NOT NULL REFERENCES skills(skill_id) ON DELETE RESTRICT,
    tenant_id text NOT NULL,
    version integer NOT NULL CHECK (version > 0),
    specification jsonb NOT NULL,
    content_hash text NOT NULL,
    created_by text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (skill_id, version),
    UNIQUE (skill_id, content_hash)
);
CREATE INDEX IF NOT EXISTS skill_versions_tenant_idx ON skill_versions (tenant_id, created_at DESC);
