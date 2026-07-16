# ADR-0014: Introduce a versioned Go control plane before replacing the runtime

## Status

Accepted

## Context

The repository already has a durable Go task gateway and a Python LangGraph
worker. It lacks enterprise resource ownership, versioned configuration,
authorization and audit records. Replacing the worker first would couple a
runtime migration to control-plane correctness and make recovery harder.

## Decision

Build the Go control plane first. PostgreSQL is the source of truth for
tenants, principals, role bindings, immutable resource versions and audit
events. RabbitMQ remains the command/event transport. Existing task execution
continues unchanged until a Go/Eino executor reaches contract parity.

Every published configuration resolves to one immutable runtime snapshot at
task admission. New configuration versions cannot change a running task.

## Consequences

- The initial control-plane release adds schema and authorization foundations
  without exposing a broad administration UI.
- API-key authentication remains only as a development and service-account
  bridge. OIDC configuration is added once issuer and audience are known.
- Runtime migration is measured with shared contract tests and a dual-run
  period; it is not a flag-day rewrite.
