"""High-level service: bridges TaskRequest → graph run → status/events.

This is the Python equivalent of the TS `agent-room/index.ts` service layer:
it owns task IDs, status materialization, and the resume-from-interrupt path.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from typing import Any

from langgraph.graph.state import CompiledStateGraph
from langgraph.types import Command

from agent_room.obs.logging import bind_task, get_logger
from agent_room.obs.tracing import TASK_ID, get_tracer
from agent_room.schemas import (
    Artifact,
    Event,
    ReviewerDecision,
    StuckDecision,
    TaskRequest,
    TaskResult,
    VerificationResult,
)
from agent_room.state import TaskState


# LangGraph's own default recursion_limit (25 supersteps) is unrelated to any
# budget this app actually governs (max_dev_rounds / max_revisions /
# max_iterations) and is far too low for this graph's shape: the ReAct
# developer's tool loop is a graph-level self-edge (developer -> ToolNode ->
# developer), so exhausting its max_dev_rounds=8 (every shipped preset) alone
# costs 2*8 + 1 = 17 supersteps. One workflow revision cycle already runs
# planner(1) + 17 + reviewer(1) = 19; the *default* max_revisions=2 needs up
# to 3 such cycles = 56 — over double LangGraph's default before the app's
# own budgets ever get a chance to produce a graceful status="failed". Sized
# here from TaskRequest's hard field ceilings (max_revisions<=10,
# max_iterations<=50) so it comfortably covers every valid request regardless
# of which preset (workflow/goal) actually runs; LangGraph's own limit should
# never fire before this app's governing budgets do.
_REACT_TURN_STEPS = 2 * 8 + 1
_FIXED_OVERHEAD = 10  # planner + delivery + a few supervisor detours + slack
GRAPH_RECURSION_LIMIT = _FIXED_OVERHEAD + (max(10, 50) + 1) * (_REACT_TURN_STEPS + 1)


def _config_for(task_id: str) -> dict[str, Any]:
    return {
        "configurable": {"thread_id": task_id},
        "recursion_limit": GRAPH_RECURSION_LIMIT,
    }


def _initial_state(task_id: str, req: TaskRequest) -> TaskState:
    return TaskState(
        task_id=task_id,
        title=req.title,
        description=req.description,
        plan=None,
        code=None,
        review=None,
        delivery=None,
        revision_round=0,
        max_revisions=req.max_revisions,
        artifacts=[],
        events=[],
        user_directives=[],
        verify_command=req.verify_command,
        verify_files=req.verify_files,
        verification=None,
        verify_round=0,
        max_iterations=req.max_iterations,
        stuck_decision=None,
    )


def _materialize_status(state: dict[str, Any], *, has_pending_tool_calls: bool = False) -> str:
    """Single source of truth for task status, derived from terminal signals.

    - pending tool-call interrupt → awaiting_user (approval mode halt).
    - `delivery` set → completed (delivery node only runs after `approved` /
      the goal oracle passing).
    - reviewer (or goal-mode supervisor, via contract reuse) asked for user
      input → awaiting_user.
    - reviewer wants more revisions but the budget is exhausted → failed.
    - goal mode: supervisor aborted, or the verify loop hit its iteration
      ceiling without the oracle passing → failed.
    - otherwise the run is still mid-flight → running.
    """

    if has_pending_tool_calls:
        return "awaiting_user"
    if state.get("delivery"):
        return "completed"
    review: ReviewerDecision | None = state.get("review")
    if review and review.decision == "need_user_decision":
        return "awaiting_user"
    if (
        review
        and review.decision == "revision_required"
        # Default must match review_router's budget check (2) — these used to
        # disagree (0 here vs 2 there), which mislabelled a mid-flight default-
        # budget run as failed the moment the reviewer asked for a revision.
        and state.get("revision_round", 0) > state.get("max_revisions", 2)
    ):
        return "failed"
    stuck = state.get("stuck_decision")
    if stuck is not None and stuck.action == "abort":
        return "failed"
    verification = state.get("verification")
    if (
        verification is not None
        and not verification.passed
        and state.get("verify_round", 0) >= state.get("max_iterations", 10)
    ):
        return "failed"
    return "running"


def _extract_pending_tool_calls(snapshot: Any) -> list[dict[str, Any]]:
    """Pull interrupt payloads off the snapshot's pending tasks.

    Returns the list of `interrupt(...)` payloads from any task that's
    paused — only the approval-wrapper sets these, so the list is the
    pending tool calls awaiting human approval. Empty when no tasks are
    halted.
    """
    pending: list[dict[str, Any]] = []
    for task in getattr(snapshot, "tasks", ()) or ():
        for interrupt_obj in getattr(task, "interrupts", ()) or ():
            value = getattr(interrupt_obj, "value", None)
            if isinstance(value, dict) and value.get("action") == "approve_tool_call":
                pending.append(value)
    return pending


def materialize_result(task_id: str, snapshot: Any) -> TaskResult:
    """Build a `TaskResult` from a LangGraph `StateSnapshot` (or a plain dict).

    Accepts either a raw dict (legacy callsites that already extracted
    `snap.values`) or a `StateSnapshot`. The snapshot path is preferred
    because it carries pending-interrupt info that a plain dict can't.
    """
    if hasattr(snapshot, "values"):
        state = dict(snapshot.values)
        pending = _extract_pending_tool_calls(snapshot)
    else:
        state = dict(snapshot)
        pending = []

    artifacts = [
        a if isinstance(a, Artifact) else Artifact.model_validate(a)
        for a in state.get("artifacts", [])
    ]
    events = [
        e if isinstance(e, Event) else Event.model_validate(e) for e in state.get("events", [])
    ]
    review_raw = state.get("review")
    if review_raw and not isinstance(review_raw, ReviewerDecision):
        review_raw = ReviewerDecision.model_validate(review_raw)
    verification_raw = state.get("verification")
    if verification_raw and not isinstance(verification_raw, VerificationResult):
        verification_raw = VerificationResult.model_validate(verification_raw)
    stuck_raw = state.get("stuck_decision")
    if stuck_raw and not isinstance(stuck_raw, StuckDecision):
        stuck_raw = StuckDecision.model_validate(stuck_raw)
    return TaskResult(
        task_id=task_id,
        status=_materialize_status(state, has_pending_tool_calls=bool(pending)),
        plan=state.get("plan"),
        code=state.get("code"),
        review=review_raw,
        delivery=state.get("delivery"),
        rounds=state.get("revision_round", 0),
        verification=verification_raw,
        stuck_decision=stuck_raw,
        artifacts=artifacts,
        events=events,
        pending_tool_calls=pending,
    )


class AgentRoomService:
    """Thin façade that owns the compiled graph and exposes ergonomic methods."""

    def __init__(self, graph: CompiledStateGraph[TaskState, Any, TaskState, TaskState]):
        self.graph = graph

    @staticmethod
    def new_task_id() -> str:
        return f"task-{uuid.uuid4().hex[:12]}"

    async def run(self, req: TaskRequest, task_id: str | None = None) -> TaskResult:
        task_id = task_id or self.new_task_id()
        config = _config_for(task_id)
        bind_task(task_id)
        log = get_logger()
        with get_tracer().start_as_current_span("agent_room.task") as span:
            span.set_attribute(TASK_ID, task_id)
            span.set_attribute("agent_room.title", req.title)
            log.info("task.start", task_id=task_id, title=req.title)
            await self.graph.ainvoke(_initial_state(task_id, req), config=config)
            result = await self.snapshot(task_id)
            log.info("task.complete", task_id=task_id, status=result.status, rounds=result.rounds)
            return result

    async def stream(
        self,
        req: TaskRequest,
        task_id: str | None = None,
    ) -> AsyncIterator[dict[str, Any]]:
        task_id = task_id or self.new_task_id()
        config = _config_for(task_id)
        async for event in self.graph.astream_events(
            _initial_state(task_id, req), config=config, version="v2"
        ):
            yield {"task_id": task_id, **event}

    async def resume(
        self,
        task_id: str,
        user_decision: str | dict[str, Any],
        *,
        at_node: str = "reviewer",
    ) -> TaskResult:
        """Continue a halted run after `need_user_decision` or a tool-call interrupt.

        The resume path depends on **which** node halted:

        - `at_node="reviewer"` (default): the legacy resume path. Rewrites
          `review` to `revision_required` so the conditional edge after
          `reviewer` routes back to `developer`. `user_decision` must be
          a string — it is appended to `state.user_directives`.
        - `at_node="planner"`: used by the `planner_gate` variant (ADR-0008
          / F2 escalation lab). The gate set `review=need_user_decision` and
          `open_questions=[...]` to halt; on resume we clear *both* so the
          `planner_gate_router` then routes to `developer` on the next step.
        - `at_node="tool_call"`: resumes from a `tool_mode: approval` halt.
          Forwards `user_decision` straight to LangGraph as `Command(resume=...)`.
          Accepted values:
            * `"approve"` / `"deny"` (string) — apply the same verdict to
              every pending tool call in the current node task.
            * `{"action": "deny", "message": "..."}` — custom denial body.
          Multi-call AIMessages are intentionally treated as one atomic
          batch in v0.3; the per-call list-of-decisions form is reserved
          for v0.4 if real workloads call for it.

        For the `reviewer` / `planner` paths, the user's decision is also
        appended to `state.user_directives` (a reducer list); the next
        developer pass reads it directly.
        """

        config = _config_for(task_id)

        if at_node == "tool_call":
            await self.graph.ainvoke(Command(resume=user_decision), config=config)
            return await self.snapshot(task_id)

        if not isinstance(user_decision, str):
            raise TypeError(
                f"resume(at_node={at_node!r}) requires a string decision; "
                f"dict decisions are only valid for at_node='tool_call'"
            )

        if at_node == "reviewer":
            update: dict[str, Any] = {
                "review": ReviewerDecision(
                    decision="revision_required",
                    feedback="See user directive in state.user_directives",
                    issues=[],
                    confidence=1.0,
                ),
                "user_directives": [user_decision],
                "events": [
                    Event(
                        type="user_decision_applied",
                        role=None,
                        payload={"decision": user_decision, "at_node": at_node},
                    )
                ],
            }
        elif at_node == "planner":
            update = {
                "review": None,
                "open_questions": [],
                "user_directives": [user_decision],
                "events": [
                    Event(
                        type="user_decision_applied",
                        role=None,
                        payload={"decision": user_decision, "at_node": at_node},
                    )
                ],
            }
        else:
            raise ValueError(
                f"resume(at_node={at_node!r}) is not supported; "
                "valid: 'reviewer' (default), 'planner', 'tool_call'"
            )

        await self.graph.aupdate_state(config, update, as_node=at_node)
        await self.graph.ainvoke(None, config=config)
        return await self.snapshot(task_id)

    async def snapshot(self, task_id: str) -> TaskResult:
        config = _config_for(task_id)
        snap = await self.graph.aget_state(config)
        return materialize_result(task_id, snap)
