CREATE TABLE IF NOT EXISTS prompt_releases (
    tenant_id text NOT NULL REFERENCES tenants(tenant_id) ON DELETE CASCADE,
    prompt_id text NOT NULL REFERENCES prompts(prompt_id) ON DELETE RESTRICT,
    environment text NOT NULL,
    baseline_version_id text NOT NULL REFERENCES prompt_versions(prompt_version_id) ON DELETE RESTRICT,
    candidate_version_id text REFERENCES prompt_versions(prompt_version_id) ON DELETE RESTRICT,
    candidate_weight integer NOT NULL DEFAULT 0 CHECK (candidate_weight BETWEEN 0 AND 100),
    updated_by text NOT NULL,
    updated_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, prompt_id, environment),
    CHECK ((candidate_version_id IS NOT NULL) OR candidate_weight = 0)
);
CREATE INDEX IF NOT EXISTS prompt_releases_tenant_idx ON prompt_releases (tenant_id, environment);
