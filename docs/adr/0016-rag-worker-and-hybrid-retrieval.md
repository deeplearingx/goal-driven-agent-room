# ADR-0016: Asynchronous RAG ingestion and frozen-profile hybrid retrieval

## Status

Accepted

## Context

Knowledge ingestion combines document parsing, chunking, remote embedding, and
database writes. Doing this in the request handler exhausts HTTP workers under
large uploads. Retrieval also needs lexical precision and semantic recall, but
their raw scores are not comparable.

## Decision

- `POST /api/v1/admin/knowledge/documents` validates an admin request and
  writes a `knowledge.ingest` command to the transactional Outbox.
- A dedicated RabbitMQ quorum queue is consumed by `knowledge-worker`; it
  chunks documents, batches remote embedding calls in groups of at most 128,
  and commits chunks plus vectors in a single PostgreSQL transaction.
- An optional `embedding_profile_version_id` freezes provider account, model,
  and parameters for both ingestion and retrieval. Vectors are keyed by this
  immutable profile-version ID, not a mutable display name.
- Provider credentials are decrypted only by trusted Worker/Gateway processes,
  cleared after use, and never serialized into command, event, audit, or HTTP
  responses. OpenAI-compatible embedding endpoints must use HTTPS and a host
  listed in `GATEWAY_EMBEDDING_ALLOWED_HOSTS` (default: `api.openai.com`).
- `POST /api/v1/knowledge/search` performs lexical FTS and, when a frozen
  embedding profile is supplied, cosine vector recall. Candidate lists are
  combined using RRF (`k=60`) with deterministic ID tie-breaking.

## Consequences

- Interactive requests are short and RabbitMQ absorbs ingestion bursts.
- A failed embedding response leaves neither new chunks nor vectors committed.
- The first vector retrieval uses exact pgvector distance ordering. Add a
  profile/dimension-specific HNSW index only after production corpus size and
  embedding dimensions are known; pgvector approximate indexes trade recall
  for speed and have dimension/operator constraints.
- The Gateway is a trusted service because synchronous semantic queries need
  temporary access to a provider credential. Deploy it with least privilege,
  secret rotation, TLS to RabbitMQ and providers, and restricted egress.
