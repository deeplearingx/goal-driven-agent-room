"""Edge routers — pure functions from `TaskState` to a routing signal.

A router returns a string label that an `EdgeSpec.branches` mapping then
resolves to a destination node (or `__end__`). Keeping the function pure
keeps `agent_room.spec` validatable at load time: no node-name strings
hard-coded inside the router, just abstract signals like `developer` /
`delivery` / `halt`.

Custom routers live elsewhere and are loaded by dotted path through
`agent_room.spec.resolve_router`. See ADR-0008.
"""

from __future__ import annotations

from typing import Literal

from agent_room.state import TaskState

ReviewSignal = Literal["developer", "delivery", "halt"]


def review_router(state: TaskState) -> ReviewSignal:
    """Default router after a `reviewer` node — migrated from v0.1 graph.py.

    Behavior (must stay equivalent to the legacy hard-coded version):
      - no review yet                   → `halt`
      - `approved`                      → `delivery`
      - `need_user_decision`            → `halt`  (parks the run; service
                                                   .resume re-enters via
                                                   `aupdate_state` + ainvoke(None))
      - `revision_required` over budget → `halt`
      - otherwise                       → `developer`
    """

    review = state.get("review")
    if review is None:
        return "halt"
    if review.decision == "approved":
        return "delivery"
    if review.decision == "need_user_decision":
        return "halt"
    if state.get("revision_round", 0) > state.get("max_revisions", 2):
        return "halt"
    return "developer"


GoalSignal = Literal["delivery", "developer", "reviewer", "supervisor", "halt"]
StuckSignal = Literal["developer", "planner", "halt"]

STUCK_EVERY = 3
"""Every this-many consecutive verification failures, the goal loop routes
through the supervisor for a strategy decision instead of mechanically
re-entering the developer. Deterministic escalation — the LLM decides *what
to change*, never *whether the loop runs*."""


def goal_router(state: TaskState) -> GoalSignal:
    """Goal-mode router after the `verify` node.

    The mechanical decisions are all deterministic (see PLAN.md goal-mode
    section's topology rationale):
      - no oracle configured          → `reviewer` (subjective judgment path)
      - oracle passed                 → `delivery` (goal achieved — THE exit)
      - loop ceiling reached          → `halt`     (runaway brake; failed)
      - every STUCK_EVERY-th failure  → `supervisor` (one strategy decision)
      - otherwise                     → `developer` (iterate with feedback)
    """

    verification = state.get("verification")
    if verification is None:
        return "reviewer"
    if verification.passed:
        return "delivery"
    verify_round = state.get("verify_round", 0)
    if verify_round >= state.get("max_iterations", 10):
        return "halt"
    if verify_round > 0 and verify_round % STUCK_EVERY == 0:
        return "supervisor"
    return "developer"


def stuck_router(state: TaskState) -> StuckSignal:
    """Routes the supervisor's `StuckDecision` (goal mode).

    `ask_user` and `abort` both map to `halt`: ask_user parks the run as
    `awaiting_user` via the need_user_decision review the supervisor wrote
    (contract reuse — see roles/supervisor.py); abort surfaces as `failed`
    via `_materialize_status`.
    """

    decision = state.get("stuck_decision")
    if decision is None:
        return "halt"
    if decision.action == "continue":
        return "developer"
    if decision.action == "replan":
        return "planner"
    return "halt"


PlannerGateSignal = Literal["developer", "halt"]


def planner_gate_router(state: TaskState) -> PlannerGateSignal:
    """Router for the planner-gate variant.

    The gate node writes a `need_user_decision` review when it detects
    open questions; this router observes that and halts before the run
    reaches the developer. Otherwise routing is unconditional.

    Halt also fires if the gate accidentally produced no plan at all —
    a defensive check for the variant's structured-output integrity.
    """

    if state.get("open_questions"):
        return "halt"
    review = state.get("review")
    if review is not None and review.decision == "need_user_decision":
        return "halt"
    if not state.get("plan"):
        return "halt"
    return "developer"
