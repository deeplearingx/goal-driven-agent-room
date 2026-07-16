# Production runbook

## Release prerequisites

- Use immutable image digests for Gateway, Python Worker, Eino Worker, and
  Knowledge Worker. All Go images must be built with Go 1.25 or newer.
- PostgreSQL must include pgvector, have automated backups and tested PITR.
- RabbitMQ must be a three-node cluster for quorum queues. Enable TLS for AMQP
  and restrict the management listener to the operations network.
- Inject OIDC, database, broker, provider and AES-256-GCM keys through the
  platform secret manager. Never expose `VITE_GATEWAY_API_KEY` in production.
- Set `GATEWAY_TRUST_TENANT_HEADER=false` unless a trusted authenticated proxy
  overwrites the header.

## Deployment order

1. Take/verify a database recovery point and record the current image digests.
2. Run the migration image once. Wait for success; the advisory lock prevents
   concurrent migrators, but it does not make an incompatible migration safe.
3. Deploy Gateway replicas with readiness probes. Keep Go Eino admission off
   until the matching Worker is healthy.
4. Deploy Python, Knowledge and optional Eino Workers. Confirm each expected
   RabbitMQ binding and that DLQ depth is zero.
5. Enable `GATEWAY_ENABLE_EINO_RUNTIME` only after the Eino queue has consumers.
6. Run the admission and idempotency k6 profiles, then one synthetic end-to-end task.
7. Record deployed digests, migration version, Eval Run ID and smoke results.

## Required production settings

```text
GATEWAY_ENV=production
GATEWAY_OIDC_ISSUER=https://...
GATEWAY_OIDC_AUDIENCE=agent-room
GATEWAY_CONFIG_ENCRYPTION_KEY=<base64 32-byte key>
GATEWAY_GUARD_MODE=warn
GATEWAY_EMBEDDING_ALLOWED_HOSTS=api.openai.com
GATEWAY_MAX_TENANT_LIMITERS=10000
GATEWAY_TRUST_TENANT_HEADER=false
```

Start Guard in `warn`, review false positives, then use `block` for sensitive
deployments. Any custom embedding host must be explicitly allowlisted and
covered by egress policy.

## Alerting baseline

- Page when readiness fails for two consecutive minutes.
- Page when the command/event DLQ is non-empty.
- Warn when `agent_room_outbox_oldest_seconds > 30`; page above 120 seconds.
- Alert when queued tasks rise while running tasks stay flat, indicating worker
  capacity or provider throttling.
- Alert on sustained failed-task growth, approval backlog, database pool
  saturation and RabbitMQ disk/memory alarms.
- Track provider quota and cost independently from infrastructure metrics.
- The exact initial SLOs, PromQL and release evidence are defined in
  [`slo-and-release-acceptance.md`](slo-and-release-acceptance.md). In
  particular, page when the 5-minute admission p99 exceeds 500ms for 15
  minutes; this derives from the Gateway's label-free admission histogram.

## Incident actions

### RabbitMQ unavailable

Keep Gateway online while PostgreSQL has capacity: task admission remains
durable in Outbox and retries use persisted exponential backoff. If outbox age
or database growth crosses the agreed limit, shed new writes at the ingress.
Do not manually republish Outbox rows.

### Provider degradation

Disable new Eino admission if only that runtime is affected. Reduce worker
concurrency to the provider quota, preserve failed tasks and use the retry API
with a new command ID after recovery.

### Bad application release

Roll application images back to the recorded digests. Do not roll the database
schema backward destructively. Migrations must be expand/contract compatible;
otherwise restore to an isolated recovery environment and follow the approved
data migration procedure.

### Suspected credential exposure

Disable the affected Provider Account, rotate the upstream key, deploy a new
configuration encryption key reference if needed, re-encrypt records through a
controlled job, and retain audit/invocation records for investigation.

## Capacity validation

Use `gateway/load/k6.js` against a non-production tenant. Record Gateway/DB/
RabbitMQ sizes, request rate, p95/p99 latency, HTTP error rate, Outbox age,
queue depth and worker concurrency. The script's 500 requests/s target is an
objective, not a benchmark claim, until reproduced on the intended topology.
Run `gateway/load/k6-idempotency.js` as well: it submits each request twice
with the same idempotency key and requires the same task identity. The full
throughput, failure-injection, soak and evidence procedure is in
[`concurrency-acceptance.md`](concurrency-acceptance.md).

## Backup and restore drill

Quarterly, restore PostgreSQL to an isolated environment, validate all schema
migrations, sample tenant isolation, replay task events, and confirm encrypted
Provider Accounts can be decrypted only with the expected key reference.
Export RabbitMQ definitions as configuration evidence; durable business state
must remain recoverable from PostgreSQL rather than depending on queue backup.

For a Compose-based non-production drill, use
[`scripts/restore-drill.ps1`](../scripts/restore-drill.ps1). It creates a
custom-format backup, restores it into a disposable pgvector container, and
checks the `tasks`, `outbox`, vector extension and migration ledger. Pass the
password at invocation time (for example from a secret manager), retain the
backup and output as the drill record, and never run it against a production
primary without the database operator's approved procedure.
