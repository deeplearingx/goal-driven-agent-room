"""Scenarios for the F2 escalation lab — under-specified tasks where the
right answer is to ASK rather than invent.

Each scenario carries a `should_escalate` flag so the lab can compute a
proper escalation rate. Scenarios that are deliberately fully specified
are kept too as a control: a variant that escalates *those* is being
overly cautious.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from agent_room.schemas import TaskRequest


@dataclass
class LabScenario:
    name: str
    task: TaskRequest
    should_escalate: bool
    """True if a healthy variant should park the run at `awaiting_user`."""

    follow_up: str | None = None
    """Optional answer to inject if the variant *does* escalate, so we can
    measure end-to-end completion (and not just escalation)."""

    notes: str = ""
    tags: list[str] = field(default_factory=list)


SCENARIOS: list[LabScenario] = [
    # --- Under-specified (should escalate) -----------------------------------
    LabScenario(
        name="under_specified_rate_limiter",
        task=TaskRequest(
            title="Rate limiter for our API client",
            description=(
                "Add a rate limiter to our outbound HTTP client. Make it production-grade."
            ),
            max_revisions=2,
        ),
        should_escalate=True,
        follow_up=(
            "Token bucket, 50 req/sec sustained, burst 100. "
            "Block (sleep) when budget exhausted, do not raise. "
            "Per-process, in-memory state is fine for now."
        ),
        notes=(
            "Production-grade is a placeholder for ~6 missing decisions: "
            "algorithm, sustained / burst rates, behaviour on exhaustion, "
            "scope (per-process / per-host), thread safety, observability."
        ),
        tags=["under_specified"],
    ),
    LabScenario(
        name="under_specified_retry_helper",
        task=TaskRequest(
            title="HTTP retry-with-backoff helper",
            description=(
                "Build a small Python helper for HTTP retry-with-backoff. "
                "It should be safe to use in our outbound API clients."
            ),
            max_revisions=2,
        ),
        should_escalate=True,
        follow_up=(
            "Retry only on 5xx and 429. Use exponential backoff with full "
            "jitter. Cap at 5 attempts. Do not retry on other 4xx."
        ),
        notes=(
            "'Safe to use' hides: which status codes retry, jitter scheme, "
            "max attempts, idempotency policy."
        ),
        tags=["under_specified"],
    ),
    LabScenario(
        name="under_specified_cache",
        task=TaskRequest(
            title="In-memory cache for our pipeline",
            description=(
                "Add a cache so we stop hitting our upstream provider for "
                "repeat queries. Keep it simple."
            ),
            max_revisions=2,
        ),
        should_escalate=True,
        follow_up=(
            "LRU, capacity 1024 entries, no TTL. Per-process. "
            "Key = (provider, query) tuple. Pickle-safe values."
        ),
        notes=(
            "'Keep it simple' hides: eviction policy, capacity, TTL, key "
            "shape, persistence, concurrency."
        ),
        tags=["under_specified"],
    ),
    # --- Fully specified (should NOT escalate, control) ----------------------
    LabScenario(
        name="control_add_two_ints",
        task=TaskRequest(
            title="add(a, b) function",
            description=(
                "Implement `add(a: int, b: int) -> int` in Python. Include "
                "a docstring and two assertion-based smoke tests."
            ),
            max_revisions=2,
        ),
        should_escalate=False,
        notes="Fully specified. Escalating here would be over-cautious.",
        tags=["control"],
    ),
    LabScenario(
        name="control_lru_cache_class",
        task=TaskRequest(
            title="LRUCache class",
            description=(
                "Implement a Python class `LRUCache(capacity: int)` with "
                "`get(key)` and `put(key, value)` methods, both O(1). "
                "Use only the standard library. Include at least one "
                "usage example with assertions."
            ),
            max_revisions=2,
        ),
        should_escalate=False,
        notes="Fully specified — every behavioural decision is in the prompt.",
        tags=["control"],
    ),
]


def filter_scenarios(names: list[str] | None) -> list[LabScenario]:
    if not names:
        return list(SCENARIOS)
    by_name = {s.name: s for s in SCENARIOS}
    out: list[LabScenario] = []
    for n in names:
        if n in by_name:
            out.append(by_name[n])
    return out
