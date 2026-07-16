"""F2 escalation lab — measures escalation rates of three variants on
under-specified tasks.

This is the lab harness for [PLAN.md §2.9](../../PLAN.md) / ADR-0008
follow-up. Variants are full presets, so the lab is also a non-trivial
end-to-end test of `build_from_spec`.

Variants under test:
  - `baseline`: v0.1 single-call reviewer ([presets/full.yaml]).
  - `planner_gate`: planner-side gate that emits `open_questions`
    structurally before the developer ever runs.
  - `two_call_review`: cheap focus-check pass before the heavy review.
  - `model_swap_<id>`: baseline pipeline but with the reviewer pinned
    to a different model id via `NodeSpec.model`.

Goals (mirrors the v0.1 smoke harness, but parametrized):
  1. Measure how often each variant escalates under-specified tasks
     to `awaiting_user` rather than inventing answers.
  2. Capture per-variant token / wall-time deltas so the cost of the
     escalation path is visible.
  3. Save raw JSONL telemetry under `snapshots/escalation/` for offline
     analysis. **Does not** auto-update findings — the human inspects
     and writes prose.

Run:
    cp .env.example .env  # then fill in real creds
    AGENT_ROOM_DB=./snapshots/escalation.db \\
        python -m examples.escalation_lab.run

Filtering:
    AGENT_ROOM_LAB_VARIANTS=planner_gate,two_call_review
    AGENT_ROOM_LAB_SCENARIOS=under_specified_rate_limiter
    AGENT_ROOM_LAB_REVIEWER_MODEL=claude-haiku-4-5-20251001  # for model_swap

Skips quietly when no provider is configured, so this stays safe to
invoke from CI / hooks. Lab calls hit a real LLM — running the full
matrix is non-trivial; use the env filters to scope a single run.
"""
