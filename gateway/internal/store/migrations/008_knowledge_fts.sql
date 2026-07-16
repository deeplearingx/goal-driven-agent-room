CREATE TABLE IF NOT EXISTS knowledge_bases (
    knowledge_base_id text PRIMARY KEY,
    tenant_id text NOT NULL REFERENCES tenants(tenant_id) ON DELETE CASCADE,
    name text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (tenant_id, name)
);

CREATE TABLE IF NOT EXISTS knowledge_documents (
    document_id text PRIMARY KEY,
    knowledge_base_id text NOT NULL REFERENCES knowledge_bases(knowledge_base_id) ON DELETE CASCADE,
    tenant_id text NOT NULL REFERENCES tenants(tenant_id) ON DELETE CASCADE,
    source_uri text NOT NULL,
    title text NOT NULL,
    content_hash text NOT NULL,
    status text NOT NULL CHECK (status IN ('pending','indexed','failed','deleted')),
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (knowledge_base_id, source_uri, content_hash)
);

CREATE TABLE IF NOT EXISTS knowledge_chunks (
    chunk_id text PRIMARY KEY,
    document_id text NOT NULL REFERENCES knowledge_documents(document_id) ON DELETE CASCADE,
    tenant_id text NOT NULL REFERENCES tenants(tenant_id) ON DELETE CASCADE,
    ordinal integer NOT NULL CHECK (ordinal >= 0),
    content text NOT NULL,
    content_hash text NOT NULL,
    metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
    fts tsvector GENERATED ALWAYS AS (to_tsvector('simple', content)) STORED,
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (document_id, ordinal),
    UNIQUE (document_id, content_hash)
);
CREATE INDEX IF NOT EXISTS knowledge_chunks_fts_idx ON knowledge_chunks USING GIN (fts);
CREATE INDEX IF NOT EXISTS knowledge_chunks_tenant_idx ON knowledge_chunks (tenant_id, created_at DESC);
