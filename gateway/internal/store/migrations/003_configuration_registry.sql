CREATE TABLE IF NOT EXISTS prompts (
    prompt_id text PRIMARY KEY,
    tenant_id text NOT NULL REFERENCES tenants(tenant_id) ON DELETE CASCADE,
    name text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (tenant_id, name)
);

CREATE TABLE IF NOT EXISTS prompt_versions (
    prompt_version_id text PRIMARY KEY,
    prompt_id text NOT NULL REFERENCES prompts(prompt_id) ON DELETE RESTRICT,
    tenant_id text NOT NULL,
    version integer NOT NULL CHECK (version > 0),
    content text NOT NULL,
    variables jsonb NOT NULL DEFAULT '{}'::jsonb,
    content_hash text NOT NULL,
    created_by text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (prompt_id, version),
    UNIQUE (prompt_id, content_hash)
);
CREATE INDEX IF NOT EXISTS prompt_versions_tenant_idx ON prompt_versions (tenant_id, created_at DESC);

CREATE TABLE IF NOT EXISTS provider_accounts (
    provider_account_id text PRIMARY KEY,
    tenant_id text NOT NULL REFERENCES tenants(tenant_id) ON DELETE CASCADE,
    provider text NOT NULL,
    name text NOT NULL,
    encrypted_secret bytea NOT NULL,
    key_reference text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    rotated_at timestamptz,
    UNIQUE (tenant_id, name)
);

CREATE TABLE IF NOT EXISTS model_profiles (
    model_profile_id text PRIMARY KEY,
    tenant_id text NOT NULL REFERENCES tenants(tenant_id) ON DELETE CASCADE,
    name text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (tenant_id, name)
);

CREATE TABLE IF NOT EXISTS model_profile_versions (
    model_profile_version_id text PRIMARY KEY,
    model_profile_id text NOT NULL REFERENCES model_profiles(model_profile_id) ON DELETE RESTRICT,
    tenant_id text NOT NULL,
    version integer NOT NULL CHECK (version > 0),
    provider_account_id text REFERENCES provider_accounts(provider_account_id) ON DELETE RESTRICT,
    model_name text NOT NULL,
    parameters jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_by text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (model_profile_id, version)
);

CREATE TABLE IF NOT EXISTS runtime_snapshots (
    runtime_snapshot_id text PRIMARY KEY,
    tenant_id text NOT NULL REFERENCES tenants(tenant_id) ON DELETE CASCADE,
    prompt_version_id text REFERENCES prompt_versions(prompt_version_id) ON DELETE RESTRICT,
    model_profile_version_id text REFERENCES model_profile_versions(model_profile_version_id) ON DELETE RESTRICT,
    agent_version jsonb NOT NULL DEFAULT '{}'::jsonb,
    tool_versions jsonb NOT NULL DEFAULT '[]'::jsonb,
    content_hash text NOT NULL,
    created_by text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (tenant_id, content_hash)
);
