"""Workflow state — the single TypedDict every node reads/writes."""

from __future__ import annotations

from collections.abc import Callable
from operator import add
from typing import Annotated, TypedDict, TypeVar

from langchain_core.messages import BaseMessage

from agent_room.schemas import (
    Artifact,
    Event,
    ReviewerDecision,
    StuckDecision,
    VerificationResult,
)

_T = TypeVar("_T")

EVENTS_SOFT_CAP = 500
"""Hard ceiling on `state.events` length — pure runaway-protection.

Typical v0.1 tasks emit 6-10 events; 500 is wildly above that. This cap is
a safety net for pathological loops, not a routine truncation mechanism.
A principled spill design for `artifacts` is deferred to v0.3 (see
[PLAN.md] RISK-9 and the upcoming tool-output spill ADR).
"""


def capped_append(cap: int) -> Callable[[list[_T] | None, list[_T] | None], list[_T]]:
    """Reducer factory: behaves like `operator.add` until `cap`, then FIFO-trims.

    Returned reducer keeps the most recent `cap` items, dropping the oldest
    on overflow. The cap is closed under repeated reductions, so it survives
    LangGraph's incremental state updates.
    """

    def _reduce(left: list[_T] | None, right: list[_T] | None) -> list[_T]:
        merged = (left or []) + (right or [])
        if len(merged) <= cap:
            return merged
        return merged[-cap:]

    _reduce.__name__ = f"capped_append_{cap}"
    return _reduce


class TaskState(TypedDict, total=False):
    """LangGraph state for a single task run.

    Reducer-annotated fields accumulate across nodes / resume cycles instead
    of being overwritten — equivalent to the TS runner's append-only
    `artifacts` array.

    - `artifacts` / `user_directives`: unbounded `add`. Keep small for v0.1
      (a handful of plan/code/delivery records per task). v0.3 will move
      large artifact bodies behind a spill store; see [PLAN.md] RISK-9.
    - `events`: capped at `EVENTS_SOFT_CAP`. FIFO trim on overflow. Cheap
      runaway-protection against pathological loops; should never fire in
      normal runs.

    Run status is *not* a stored field. It is derived in
    `agent_room.service._materialize_status` from the presence of `delivery`,
    the `review.decision`, and `revision_round` vs `max_revisions`. Storing
    it would create two sources of truth that drift apart.
    """

    task_id: str
    title: str
    description: str

    plan: str | None
    code: str | None
    review: ReviewerDecision | None
    delivery: str | None

    revision_round: int
    max_revisions: int

    artifacts: Annotated[list[Artifact], add]
    events: Annotated[list[Event], capped_append(EVENTS_SOFT_CAP)]

    user_directives: Annotated[list[str], add]

    open_questions: list[str]
    """Set by the planner-gate / two-call-reviewer variants when a node
    detects an under-specified task. Non-empty → the run halts at
    `awaiting_user` so a human can answer before code is written.

    Overwrite-on-write (no reducer): each time the planner / reviewer runs
    it produces an authoritative list for *this round*. Prior questions
    that were answered get replaced by an empty list, not accumulated.
    """

    dev_messages: Annotated[list[BaseMessage], add]
    """Developer ReAct working memory. Only the developer subgraph reads /
    writes this — reviewer / delivery do not touch it.

    Append-only via `add`: ToolNode emits `[ToolMessage(...)]` increments and
    the developer agent emits its own `AIMessage` increments. v0.4's
    ContextEngine will gain authority to compress this; for v0.3 we accept
    unbounded growth (RISK-9 already tracks this).
    """

    dev_round: int
    """Developer ReAct loop counter. Caps tool-using turns; once it hits
    `NodeSpec.max_dev_rounds` the developer node stops binding tools so
    the LLM has to emit final code instead of another tool call.

    Goal mode: the verifier / reviewer / planner reset this to 0 when they
    push new information into `dev_messages` (see the transcript-push
    pattern in roles/verifier.py's docstring), so each iteration gets a
    fresh tool budget instead of inheriting a possibly-exhausted one.
    """

    verify_command: str | None
    """Goal mode — the objective oracle command (from TaskRequest). `None`
    routes verification to the reviewer's subjective judgment instead."""

    verify_files: dict[str, str]
    """Goal mode — files re-seeded into the workspace before every
    verification run (anti-tamper; see TaskRequest.verify_files)."""

    verification: VerificationResult | None
    """Goal mode — latest oracle run's outcome. Overwrite-on-write (no
    reducer), same authority pattern as `review`."""

    verify_round: int
    """Goal mode — count of verification runs so far; `goal_router` halts
    once this reaches `max_iterations` (the runaway brake)."""

    max_iterations: int
    """Goal mode — loop ceiling, from TaskRequest.max_iterations."""

    stuck_decision: StuckDecision | None
    """Goal mode — the supervisor's latest strategy call, read by
    `stuck_router`. Overwrite-on-write."""
