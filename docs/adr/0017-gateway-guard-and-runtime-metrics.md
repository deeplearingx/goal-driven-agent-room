# ADR-0017: Gateway input guard and task lifecycle metrics

## Status

Accepted

## Decision

- The Go Gateway applies a deterministic input guard before writing a task to
  the Outbox. `GATEWAY_GUARD_MODE` supports `off`, `warn`, and `block`; its
  production default is `warn`.
- The guard detects a deliberately narrow set of prompt-injection and exposed
  credential patterns. It records rule metadata, never the matched input.
- A blocking decision is independently audited even though no task exists. A
  warning is attached to the task-create audit record.
- `/metrics` exports outbox backlog/age plus queued, running, awaiting-user,
  and failed task gauges. Alerting should use these lifecycle signals together:
  high queued with low running indicates worker capacity; high awaiting-user
  indicates an approval bottleneck; sustained failures indicate runtime or
  provider degradation.

## Consequences

- The gateway prevents obvious unsafe requests from reaching either runtime.
- Pattern guards are not semantic safety classifiers; provider moderation and
  the Python runtime's tool/response/output checkpoints remain required.
- Metrics contain no tenant, prompt, task, or user labels, avoiding high-cardinality
  Prometheus series and inadvertent data exposure.
