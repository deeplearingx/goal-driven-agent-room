"""Escalation suite — behavioural tasks for the planner_gate ablation.

Under-specified tasks where the right move is to ASK, not invent, plus a fully-
specified control (escalating *that* is over-caution). Scored behaviourally:
pass iff the run parks at `awaiting_user` exactly when it should. This is the
axis a correctness oracle can't measure — the spec is deliberately ambiguous.

Lifted from the F2 escalation lab (`examples/escalation_lab/scenarios.py`),
re-expressed as harness `BehavioralTask`s so the unified runner produces the
ablation table.
"""

from __future__ import annotations

from agent_room.eval import BehavioralTask
from agent_room.schemas import TaskRequest

ESCALATION_TASKS: list[BehavioralTask] = [
    BehavioralTask(
        name="underspec_rate_limiter",
        request=TaskRequest(
            title="Rate limiter for our API client",
            description=(
                "Add a rate limiter to our outbound HTTP client. Make it production-grade."
            ),
            max_revisions=2,
        ),
        should_escalate=True,
        follow_up=(
            "Token bucket, 50 req/sec sustained, burst 100. Block (sleep) when "
            "budget exhausted, do not raise. Per-process, in-memory state is fine."
        ),
        tags=["under_specified"],
    ),
    BehavioralTask(
        name="underspec_retry_helper",
        request=TaskRequest(
            title="HTTP retry-with-backoff helper",
            description=(
                "Build a small Python helper for HTTP retry-with-backoff. "
                "It should be safe to use in our outbound API clients."
            ),
            max_revisions=2,
        ),
        should_escalate=True,
        follow_up=(
            "Retry only on 5xx and 429. Exponential backoff with full jitter. "
            "Cap at 5 attempts. Do not retry on other 4xx."
        ),
        tags=["under_specified"],
    ),
    BehavioralTask(
        name="underspec_cache",
        request=TaskRequest(
            title="In-memory cache for our pipeline",
            description=(
                "Add a cache so we stop hitting our upstream provider for repeat "
                "queries. Keep it simple."
            ),
            max_revisions=2,
        ),
        should_escalate=True,
        follow_up=(
            "LRU, capacity 1024 entries, no TTL. Per-process. Key = (provider, query) tuple."
        ),
        tags=["under_specified"],
    ),
    BehavioralTask(
        name="control_add_two_ints",
        request=TaskRequest(
            title="add(a, b) function",
            description=(
                "Implement `add(a: int, b: int) -> int` in Python. Include a "
                "docstring and two assertion-based smoke tests."
            ),
            max_revisions=2,
        ),
        should_escalate=False,
        tags=["control"],
    ),
]
