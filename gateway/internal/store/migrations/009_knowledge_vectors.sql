CREATE EXTENSION IF NOT EXISTS vector;

-- Keep embeddings separate from lexical chunks so a document can be reindexed
-- with a new model without rewriting FTS content. Dimensions are recorded and
-- validated by the ingest worker; each model gets its own retrieval path.
CREATE TABLE IF NOT EXISTS knowledge_embeddings (
    chunk_id text NOT NULL REFERENCES knowledge_chunks(chunk_id) ON DELETE CASCADE,
    tenant_id text NOT NULL REFERENCES tenants(tenant_id) ON DELETE CASCADE,
    model_name text NOT NULL,
    dimensions integer NOT NULL CHECK (dimensions > 0 AND dimensions <= 8192),
    embedding vector NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (chunk_id, model_name)
);
CREATE INDEX IF NOT EXISTS knowledge_embeddings_tenant_model_idx ON knowledge_embeddings (tenant_id, model_name);
