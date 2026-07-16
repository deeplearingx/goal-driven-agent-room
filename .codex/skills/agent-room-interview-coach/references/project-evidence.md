# Agent Room Project Evidence Map

Use this as a navigation index, then verify claims against current source.

## One-sentence position

Agent Room is a centralized, controllable, and evaluable multi-role coding-agent system built on LangGraph. A state machine coordinates planner, tool-using developer, reviewer/verifier, supervisor, and delivery roles; it is deliberately not a decentralized agent swarm.

Primary sources: `docs/portfolio.md`, `docs/architecture.md`, `agent_room/graph.py`, `agent_room/presets/goal.yaml`.

## Architecture and execution

- Workflow mode follows planner -> developer -> reviewer -> delivery, with revision and human-decision branches.
- Goal mode changes termination semantics: develop -> objective verify repeats until the oracle passes; `max_iterations` is a runaway brake.
- Mechanical routing stays deterministic in `agent_room/routers.py`; every third verification failure may detour once through an LLM supervisor for continue/replan/ask-user/fail strategy.
- `TaskState` is the cross-node source of truth. Append-only reducer fields include events, artifacts, directives, and the developer transcript.
- SQLite-backed LangGraph checkpoints support snapshots and resume. The service must retain the per-request graph preset across later resume/snapshot operations.
- FastAPI exposes detached SSE runs with replay, heartbeat, cancellation, and UI rehydration.

Inspect: `agent_room/state.py`, `agent_room/routers.py`, `agent_room/service.py`, `agent_room/server/run_manager.py`, `agent_room/server/api.py`.

## Signature failure investigation

The difficult goal task exposed an infrastructure failure, not simply weak model reasoning.

1. A 50-iteration task exceeded LangGraph's recursion limit even though it was still within the product iteration budget.
2. The original formula counted main-loop work but undercounted periodic supervisor/replan supersteps. Valid paths could therefore raise `GraphRecursionError`.
3. Windows command parsing used POSIX `shlex` behavior in a path-sensitive flow, corrupting absolute paths.
4. Request-selected goal graphs were not reliably preserved for later operations, so resume/snapshot could use the wrong graph instance.
5. The repaired recursion budget explicitly covers worst-case graph overhead rather than treating framework supersteps as product iterations.

Evidence and exact reproduction: `docs/findings/2026-07-04-goal-mode-hard-task-failure.md`, `agent_room/service.py`, `tests/test_goal_hard_task.py`, `tests/test_goal_graph_persistence.py`.

## Feedback-loop repair

The ReAct developer transcript is append-only. Earlier revisions could replay an old transcript without seeing newly generated reviewer/verifier/planner feedback. The transcript-push rule appends new human messages to `dev_messages` and resets the local developer round, so subsequent tool-using turns receive the new evidence.

Inspect: `agent_room/roles/developer_react.py`, `agent_room/roles/reviewer.py`, `agent_room/roles/verifier.py`, `agent_room/roles/planner.py`, `agent_room/state.py`.

## Objective verification and anti-cheating

- `verify_command` is executed by a non-LLM verifier in the task workspace.
- It uses the same command allowlist and path confinement as developer tools and has a timeout.
- Verification files can be reseeded before every run so the developer cannot win by editing the test oracle.
- Without `verify_command`, goal mode falls back to reviewer judgment; say this limitation plainly.

Inspect: `agent_room/roles/verifier.py`, `agent_room/tools/_safety.py`, `agent_room/presets/goal.yaml`.

## Live evidence

The repaired path was tested with the configured DeepSeek model through an Anthropic-compatible endpoint. A deliberately underspecified median task failed verification on round one, incorporated the verifier-only convention, and passed on round two. This demonstrates feedback propagation on one real task; it is not a broad model benchmark.

The recorded local quality gate after repair is 616 passed, 1 skipped, Ruff clean, and strict mypy across 76 files. Re-check before citing if the repository changes.

Evidence: `docs/findings/2026-07-04-deepseek-live-validation.md`, `snapshots/real-goal-da4f59ee`, test configuration in `pyproject.toml`.

## Differentiating tradeoffs

- Centralized orchestration was chosen for traceability, reproducibility, controlled branching, and ablation—not because maximum autonomy is always best.
- Deterministic code owns mechanical decisions; LLM judgment is used for ambiguous strategy or structured review.
- Capabilities are optional with NoOp defaults, enabling ablation and backward-compatible adoption.
- Context compression preserves the middle through summary instead of dropping it; token-pressure triggering outperformed message-count triggering in the recorded experiment.
- Curated memory and searchable transcript memory have different semantics. Session-frozen curated prompts protect prefix caching; new facts become prompt-visible next session.
- Physical per-tenant storage isolation does not provide identity or authorization. A trusted gateway must derive tenant identity.

Evidence: `docs/portfolio.md`, `docs/context-engineering.md`, `docs/memory.md`, `agent_room/server/tenancy.py`, `SECURITY.md`.

## Honest limitations

- Live-model evaluations are small-N and primarily one provider.
- SQLite and embedded storage constrain horizontal scale and multi-process coordination.
- Budget tracking may reset on resume depending on the configured lifecycle.
- Tenant IDs are caller-supplied unless a trusted ingress supplies them.
- Objective quality depends on the strength and immutability of the verification oracle.
- Central orchestration trades emergent autonomy for control and observability.

