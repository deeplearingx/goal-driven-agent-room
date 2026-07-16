# Go + RabbitMQ production architecture

This deployment keeps LangGraph and model/tool execution in Python, where the
project's domain code already lives. A Go control plane owns the high-concurrency
HTTP path and RabbitMQ absorbs slow, bursty LLM work.

```text
Browser/API client
      │ HTTP + SSE
      ▼
Go gateway ── transaction ── PostgreSQL tasks + outbox
      │                              ▲
      │ publisher confirm            │ events/results + LISTEN/NOTIFY
      ▼                              │
RabbitMQ quorum queues ─────► Python LangGraph workers
      │                              │
      └── event projector queue ◄────┘
```

## Why this is more than “RabbitMQ connected”

- Task creation and the outbox record commit in one PostgreSQL transaction. If
  the gateway crashes after the HTTP response, the outbox dispatcher still
  publishes the command. Publisher confirms and `mandatory` routing detect a
  broker rejection or missing binding.
- Job and event queues are durable quorum queues. Consumers use manual
  acknowledgements, bounded prefetch, persistent messages, retry delivery limits,
  and dead-letter queues.
- RabbitMQ is at-least-once. `Idempotency-Key` deduplicates client retries, the
  Go event projector deduplicates event `message_id`, and `worker_inbox` leases
  suppress duplicate LLM executions. Before an LLM call, a worker atomically
  claims a `queued` task as `running` and rechecks the database state. A task
  cancelled while waiting in RabbitMQ is acknowledged without executing.
- LangGraph checkpoints use PostgreSQL, so resume/redelivery can land on any
  worker. SQLite remains supported for the original single-process developer
  mode only.
- SSE events are persisted before delivery. `Last-Event-ID`/`from` replays after
  disconnects; PostgreSQL `LISTEN/NOTIFY` wakes every gateway replica without a
  polling query per idle connection.
- Per-tenant token-bucket limiting, body ceilings, tenant-scoped reads, optional
  bearer auth, bounded DB pools, security headers, RabbitMQ-aware readiness,
  structured logs, retention cleanup, and Prometheus-text metrics are present
  on the admission path.

## Run locally

Set one supported model provider key in `.env`, then:

```bash
docker compose -f docker-compose.production.yml up --build --scale worker=4
```

Compose runs a short-lived `migrate` service before the gateway and workers.
The migration code takes a PostgreSQL advisory lock, so rollout replicas cannot
apply schema setup concurrently. Do not enable `GATEWAY_AUTO_MIGRATE` on every
gateway pod in a real deployment.

For the existing Vite UI, set `VITE_API_BASE_URL=http://localhost:8080`. The
gateway keeps the current task/SSE/session/workspace routes compatible, in
addition to the versioned `/api/v1/tasks` admission API.

`VITE_GATEWAY_API_KEY` is intentionally documented for local development only:
anything prefixed with `VITE_` is embedded in the browser bundle. In production
place the UI behind an OIDC-capable reverse proxy or BFF that authenticates the
browser and supplies a trusted identity. By default the gateway ignores
`X-Tenant-ID`; set `GATEWAY_TRUST_TENANT_HEADER=true` only when that header is
injected by such a trusted proxy.

Create and follow a task:

```bash
curl -i -X POST http://localhost:8080/api/v1/tasks \
  -H "Content-Type: application/json" \
  -H "X-Tenant-ID: demo" \
  -H "Idempotency-Key: order-42" \
  -d '{"title":"FizzBuzz","description":"Implement it with tests"}'

curl -N http://localhost:8080/api/v1/tasks/TASK_ID/events \
  -H "X-Tenant-ID: demo"
```

RabbitMQ management is bound to `127.0.0.1:15672`. Gateway health/readiness are
`/healthz` and `/readyz`; metrics are at `/metrics` (authenticated unless
`GATEWAY_METRICS_PUBLIC=true`). `GATEWAY_RETENTION` controls cleanup of old
terminal tasks, delivered outbox rows and completed worker inbox rows. LangGraph
checkpoint retention should be operated separately.

Failed tasks can be retried through `POST /api/v1/tasks/<id>/retry`. The gateway
creates a fresh command ID and durable Outbox record, so the worker inbox does
not mistake an operator retry for a duplicate delivery. Keep the original DLQ
message as forensic evidence; use the task retry endpoint rather than manually
republishing a stale message ID.

## Capacity validation

The included k6 scenario measures the admission path separately from paid LLM
latency:

```bash
k6 run gateway/load/k6.js -e BASE_URL=http://localhost:8080
```

`RATE`, `DURATION`, and `TASK_PREFIX` make the same script repeatable at a
smaller, environment-safe level. For example, the local admission check uses
`RATE=20 DURATION=15s TASK_PREFIX=capacity-YYYYMMDD`. Stop workers first,
mark the uniquely prefixed tasks as cancelled after the run, then restart
workers so no synthetic request reaches a paid model.

Its default target ramps to 500 accepted tasks/s with p95 under 250 ms. Treat
that as a test objective, not a benchmark claim: record results on the intended
CPU, PostgreSQL, RabbitMQ topology, request size, and worker/model limits.

Scale gateway replicas for HTTP/SSE connections and worker replicas for LLM
throughput. Keep worker prefetch close to worker concurrency; raising it far
above available execution slots merely moves backlog into worker memory.

## Kubernetes baseline

`deploy/kubernetes/` provides a production-oriented starting point: three
Gateway replicas behind a Service with readiness/liveness probes and an HPA,
plus Worker disruption protection and a KEDA `ScaledObject` that uses
`agent-room.jobs` queue depth. Supply immutable image tags and a real secret
outside source control, apply `migration-job.yaml` for each release, then
apply the Kustomization. PostgreSQL and RabbitMQ are deliberately external,
managed dependencies; the local Compose instances are not a HA topology.

```bash
kubectl apply -f deploy/kubernetes/app-secret.yaml
kubectl apply -f deploy/kubernetes/migration-job.yaml
kubectl wait --for=condition=complete job/agent-room-migrate-RELEASE -n agent-room
kubectl apply -k deploy/kubernetes
```

KEDA must be installed before applying `worker.yaml`. Its queue threshold of
20 is intentionally conservative and should be calibrated against observed
model latency, worker concurrency, and provider quotas.

## Production hardening still owned by the deployer

- The Compose topology defaults to `pgvector/pgvector:pg17` so migration 009
  can create the `vector` extension. Existing deployments using plain
  PostgreSQL must perform the normal database-image upgrade and backup/PITR
  check before applying that migration; do not point a major-version image at
  an incompatible data directory.

- Prefer `GATEWAY_OIDC_ISSUER` and `GATEWAY_OIDC_AUDIENCE` in browser-facing
  production deployments. The Gateway verifies the signature, issuer and
  audience, then obtains tenant and roles from the configured claims. A
  `viewer` is read-only; `operator`, `service`, and `tenant_admin` can submit
  or transition tasks. `GATEWAY_API_KEY` remains suitable for trusted
  service-to-service calls and local development.
- Put TLS/OIDC and a WAF at the ingress; set `GATEWAY_API_KEY` only as the simple
  service-to-service option and never rely on caller-provided tenant identity
  without an authenticated mapping.
- Provider API keys are write-only configuration. Set
  `GATEWAY_CONFIG_ENCRYPTION_KEY` to a base64-encoded 32-byte key injected from
  a KMS-backed secret and set `GATEWAY_CONFIG_ENCRYPTION_KEY_ID` to its rotation
  reference. The Gateway encrypts each submitted credential with AES-256-GCM,
  never returns it from the API, and refuses provider-account writes when the
  encryption key is absent. Rotate by deploying a new key reference and
  re-encrypting records through a controlled migration job.
- `GATEWAY_ENABLE_EINO_RUNTIME` defaults to `false`. Enable it only after a
  matching 64-bit Go worker is deployed, monitored and routed to the Go
  command queue. Each accepted task carries an immutable
  `execution_runtime`; disabling the flag blocks new Eino tasks but does not
  reinterpret already queued work. See ADR-0015.
- To deploy the first Go executor, set the configuration encryption key and
  run `docker compose -f docker-compose.production.yml --profile eino up -d
  --build`. The `eino-worker` has access to encrypted provider account records
  but has no HTTP listener; it consumes only `run.go_eino` commands.
- Run a three-node RabbitMQ cluster for quorum queues and a highly available
  PostgreSQL service with backups/PITR. The compose file is a local topology.
- Use per-tenant budgets/quotas, secret management, audit retention, alerting on
  DLQs/outbox age/queue depth, and autoscaling based on queue age rather than CPU
  alone.
- Workspace files are currently on a shared Docker volume. Kubernetes or
  multi-host deployments should replace it with object storage or a tenant-safe
  shared filesystem before allowing arbitrary cross-worker resume.
