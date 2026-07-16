"""Regression tests for the F2 escalation experiment variants.

Validates routing / state-update behaviour of the three v0.2 §2.9 paths:
  - `planner_gate` preset (planner-side gate)
  - `two_call_review` preset (cheap focus-check first, full review only if
    fully specified)
  - per-node `model:` override (the third option from ADR-0008 §F2 carry-
    forward — fully covered by `tests/test_spec.py::test_node_prompt_override`
    + `RoleBindings.resolve` already; no extra route to test here).

These are *deterministic* tests that prove the wiring works under fakes —
they do NOT measure escalation rates against a live LLM. That measurement
runs out of `examples/escalation_lab/` and is recorded in
[docs/findings/2026-06-11-smoke-v0.1.md] when executed.
"""

from __future__ import annotations

import pytest
from langchain_core.language_models.fake_chat_models import FakeListChatModel

from agent_room.config import RoleBindings
from agent_room.graph import build_from_spec
from agent_room.schemas import (
    FocusCheck,
    PlannerGateOutput,
    ReviewerDecision,
    TaskRequest,
)
from agent_room.service import AgentRoomService
from agent_room.spec import GraphSpec, load_preset
from tests.fakes import FakeReviewerLLM, bindings_with_fakes

# --- planner_gate ---------------------------------------------------------


def _gate_planner_llm(outputs: list[PlannerGateOutput]) -> FakeReviewerLLM:
    """A fake planner whose `with_structured_output(PlannerGateOutput)` queue
    returns the given list."""

    fake = FakeReviewerLLM(responses=["unused"])
    fake.outputs_by_schema = {PlannerGateOutput: outputs}
    return fake


@pytest.mark.asyncio
async def test_planner_gate_halts_when_questions_open() -> None:
    """Gate sees ambiguity → run halts at `awaiting_user` before developer runs."""

    spec = load_preset("planner_gate")
    bindings = RoleBindings(
        planner=_gate_planner_llm(
            [
                PlannerGateOutput(
                    plan="1. step",
                    open_questions=["which library?", "rate limit?"],
                    rationale="ambiguous task",
                )
            ]
        ),
        # Developer should never be called; if it is, this list is empty
        # and FakeListChatModel will raise — making the test fail loudly.
        developer=FakeListChatModel(responses=[]),
        reviewer=FakeReviewerLLM(responses=["unused"]),
        delivery=FakeListChatModel(responses=[]),
    )
    graph = build_from_spec(spec, bindings)
    service = AgentRoomService(graph)

    result = await service.run(TaskRequest(title="t", description="d"))

    assert result.status == "awaiting_user"
    assert result.code is None  # developer never ran
    assert result.delivery is None
    assert result.review is not None
    assert result.review.decision == "need_user_decision"
    assert "which library?" in result.review.feedback
    assert "rate limit?" in result.review.feedback


@pytest.mark.asyncio
async def test_planner_gate_proceeds_when_no_questions() -> None:
    """Gate has no questions → run proceeds normally to delivery."""

    spec = load_preset("planner_gate")
    bindings = RoleBindings(
        planner=_gate_planner_llm([PlannerGateOutput(plan="1. step\n2. ship", open_questions=[])]),
        developer=FakeListChatModel(responses=["impl"]),
        reviewer=FakeReviewerLLM(
            responses=["unused"],
            decisions=[ReviewerDecision(decision="approved", feedback="ok", confidence=0.95)],
        ),
        delivery=FakeListChatModel(responses=["# Delivered"]),
    )
    graph = build_from_spec(spec, bindings)
    service = AgentRoomService(graph)

    result = await service.run(TaskRequest(title="t", description="d"))

    assert result.status == "completed"
    assert result.code == "impl"
    assert result.delivery == "# Delivered"


@pytest.mark.asyncio
async def test_planner_gate_resume_at_planner_continues() -> None:
    """Halt at planner → resume(at_node='planner') feeds answers and proceeds."""

    spec = load_preset("planner_gate")
    bindings = RoleBindings(
        planner=_gate_planner_llm(
            [
                # First plan: ambiguous, halts.
                PlannerGateOutput(
                    plan="1. step",
                    open_questions=["A or B?"],
                ),
                # After resume the gate runs again with directives in scope;
                # this time it produces a clean plan with no open questions.
                PlannerGateOutput(plan="1. resolved\n2. ship", open_questions=[]),
            ]
        ),
        developer=FakeListChatModel(responses=["impl after answer"]),
        reviewer=FakeReviewerLLM(
            responses=["unused"],
            decisions=[ReviewerDecision(decision="approved", feedback="ok", confidence=0.9)],
        ),
        delivery=FakeListChatModel(responses=["# Delivered"]),
    )
    graph = build_from_spec(spec, bindings)
    service = AgentRoomService(graph)

    task_id = service.new_task_id()
    paused = await service.run(TaskRequest(title="t", description="d"), task_id=task_id)
    assert paused.status == "awaiting_user"

    resumed = await service.resume(task_id, "use approach A", at_node="planner")
    assert resumed.status == "completed"
    assert resumed.delivery == "# Delivered"

    snap = await service.graph.aget_state({"configurable": {"thread_id": task_id}})
    assert snap.values.get("user_directives") == ["use approach A"]
    assert snap.values.get("open_questions") == []


# --- two_call_review ------------------------------------------------------


def _two_call_reviewer(
    *,
    focus: list[FocusCheck],
    decisions: list[ReviewerDecision] | None = None,
) -> FakeReviewerLLM:
    fake = FakeReviewerLLM(responses=["unused"])
    fake.outputs_by_schema = {
        FocusCheck: focus,
        ReviewerDecision: decisions or [],
    }
    return fake


@pytest.mark.asyncio
async def test_two_call_reviewer_escalates_on_focus_check() -> None:
    """Focus check says under-specified → reviewer emits `need_user_decision`
    without calling the heavyweight review LLM."""

    spec = load_preset("two_call_review")
    bindings = bindings_with_fakes(
        plan_response="plan",
        code_responses=["code"],
        delivery_response="(unused)",
    )
    bindings.reviewer = _two_call_reviewer(
        focus=[
            FocusCheck(
                is_fully_specified=False,
                open_questions=["retry semantics?"],
                rationale="ambiguous",
            )
        ],
        decisions=[],  # never called on this path
    )
    graph = build_from_spec(spec, bindings)
    service = AgentRoomService(graph)

    result = await service.run(TaskRequest(title="t", description="d"))

    assert result.status == "awaiting_user"
    assert result.review is not None
    assert result.review.decision == "need_user_decision"
    assert "retry semantics?" in result.review.feedback


@pytest.mark.asyncio
async def test_two_call_reviewer_proceeds_when_fully_specified() -> None:
    """Focus check passes → second call runs and approval flows through."""

    spec = load_preset("two_call_review")
    bindings = bindings_with_fakes(
        plan_response="plan",
        code_responses=["code"],
        delivery_response="# Delivered",
    )
    bindings.reviewer = _two_call_reviewer(
        focus=[FocusCheck(is_fully_specified=True, open_questions=[])],
        decisions=[ReviewerDecision(decision="approved", feedback="ok", confidence=0.9)],
    )
    graph = build_from_spec(spec, bindings)
    service = AgentRoomService(graph)

    result = await service.run(TaskRequest(title="t", description="d"))

    assert result.status == "completed"
    assert result.delivery == "# Delivered"


@pytest.mark.asyncio
async def test_two_call_reviewer_skips_focus_check_after_directives() -> None:
    """Once `user_directives` exist, the focus check is bypassed and the
    reviewer goes straight to the normal review — directives mean a human
    already answered the open questions."""

    spec = load_preset("two_call_review")
    bindings = bindings_with_fakes(
        plan_response="plan",
        code_responses=["v1", "v2"],
        delivery_response="# Final",
    )
    # Round 1: focus says under-specified → escalates.
    # Round 2 (after resume): focus would say under-specified again, but the
    # variant must skip the check because directives are present.
    bindings.reviewer = _two_call_reviewer(
        focus=[
            FocusCheck(
                is_fully_specified=False,
                open_questions=["retry policy?"],
                rationale="ambiguous",
            ),
            FocusCheck(
                is_fully_specified=False,
                open_questions=["irrelevant — should be skipped"],
                rationale="…",
            ),
        ],
        decisions=[
            # Only used after directives exist.
            ReviewerDecision(decision="approved", feedback="ok with directive", confidence=0.95),
        ],
    )
    graph = build_from_spec(spec, bindings)
    service = AgentRoomService(graph)
    task_id = service.new_task_id()

    paused = await service.run(TaskRequest(title="t", description="d"), task_id=task_id)
    assert paused.status == "awaiting_user"

    resumed = await service.resume(task_id, "retry only on 5xx")
    assert resumed.status == "completed"
    assert resumed.delivery == "# Final"


# --- variant resolution ---------------------------------------------------


def test_unknown_variant_raises_at_build_time() -> None:
    spec = GraphSpec.model_validate(
        {
            "name": "bad-variant",
            "entry": "developer",
            "nodes": {"developer": {"role": "developer", "variant": "ghost"}},
            "edges": [{"from": "developer", "to": "__end__"}],
        }
    )
    with pytest.raises(ValueError, match="ghost"):
        build_from_spec(spec, RoleBindings())
