# ADR-0015: Dual runtime rollout for Go/Eino

## Status

Accepted for incremental rollout.

## Context

The existing Python LangGraph worker has production task/checkpoint semantics.
A direct replacement with a new Go runtime would make failure attribution and
rollback ambiguous. Eino provides the Go component and ReAct abstractions that
we need, but it must be introduced without changing already accepted tasks.

## Decision

- A task command has an immutable `execution_runtime`: `python_langgraph` or
  `go_eino`.
- `python_langgraph` is the default and remains the only accepted selection
  until `GATEWAY_ENABLE_EINO_RUNTIME=true` is deployed alongside healthy Go
  workers.
- The Gateway writes the chosen runtime and complete runtime snapshot into the
  outbox command in the same transaction as task creation.
- Workers consume only their own runtime route. They must reject a command
  whose snapshot or runtime does not match their startup configuration.
- Turning the feature flag off immediately prevents new Go tasks; queued and
  running tasks keep their original runtime. Rollback therefore never changes
  execution semantics for an accepted task.

## Consequences

The first Go worker can be canaried by selecting `go_eino` for an explicit
tenant or release cohort. Cross-runtime result comparison and automatic
promotion are deferred to the evaluation workstream.
