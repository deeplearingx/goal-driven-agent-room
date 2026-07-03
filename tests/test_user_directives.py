"""Regression tests for status derivation + user_directives resume protocol.

These tests guard the v0.1 architectural cleanup:
- Status is derived from terminal signals, not stored in state.
- `resume()` puts the user's instruction into `state.user_directives` and the
  developer node reads it explicitly (no smuggling through `review.feedback`).
- `events` reducer is FIFO-capped (runaway protection; see PLAN.md RISK-9).
"""

from __future__ import annotations

import pytest

from agent_room.graph import build_agent_room_graph
from agent_room.schemas import Event, ReviewerDecision, TaskRequest
from agent_room.service import AgentRoomService, _materialize_status
from agent_room.state import EVENTS_SOFT_CAP, capped_append
from tests.fakes import CapturingFakeChatModel, bindings_with_fakes


def test_materialize_status_does_not_read_status_field() -> None:
    """Snapshot synthesized with a stale `status` key must not influence output."""

    snapshot_with_stale_status = {
        "status": "running",  # stale leftover; new code must ignore it
        "delivery": "# Done",
    }
    assert _materialize_status(snapshot_with_stale_status) == "completed"

    snapshot_awaiting = {
        "status": "completed",  # also stale; reviewer signal takes precedence
        "review": ReviewerDecision(decision="need_user_decision", feedback="?"),
    }
    assert _materialize_status(snapshot_awaiting) == "awaiting_user"


def test_materialize_status_failed_when_revisions_exhausted() -> None:
    snapshot = {
        "review": ReviewerDecision(decision="revision_required", feedback="no"),
        "revision_round": 3,
        "max_revisions": 2,
    }
    assert _materialize_status(snapshot) == "failed"


@pytest.mark.asyncio
async def test_resume_routes_user_directive_through_state() -> None:
    """The directive must land in `state.user_directives` and reach developer prompt.

    Captures the developer's prompts on round 2 and asserts the directive
    string is present verbatim — proves we are *not* relying on
    `review.feedback` to carry user input.
    """

    capturing_dev = CapturingFakeChatModel(responses=["v1 code", "v2 code"])
    bindings = bindings_with_fakes(
        developer=capturing_dev,
        decisions=[
            ReviewerDecision(decision="need_user_decision", feedback="A or B?"),
            ReviewerDecision(decision="approved", feedback="ok", confidence=0.95),
        ],
        delivery_response="# Final",
    )
    graph = build_agent_room_graph(bindings)
    service = AgentRoomService(graph)

    task_id = service.new_task_id()
    paused = await service.run(TaskRequest(title="t", description="d"), task_id=task_id)
    assert paused.status == "awaiting_user"

    resumed = await service.resume(task_id, "use approach A: prefer stdlib")
    assert resumed.status == "completed"
    assert resumed.delivery == "# Final"

    snap = await service.graph.aget_state({"configurable": {"thread_id": task_id}})
    assert snap.values.get("user_directives") == ["use approach A: prefer stdlib"]

    second_round_prompt = capturing_dev.captured_prompts[-1]
    assert "use approach A: prefer stdlib" in second_round_prompt
    assert "User directives" in second_round_prompt


@pytest.mark.asyncio
async def test_resume_appends_multiple_directives_in_order() -> None:
    """Reducer-list semantics: a second pause/resume cycle accumulates directives."""

    bindings = bindings_with_fakes(
        code_responses=["v1", "v2", "v3"],
        decisions=[
            ReviewerDecision(decision="need_user_decision", feedback="?"),
            ReviewerDecision(decision="need_user_decision", feedback="?"),
            ReviewerDecision(decision="approved", feedback="ok"),
        ],
        delivery_response="# Done",
    )
    graph = build_agent_room_graph(bindings)
    service = AgentRoomService(graph)
    task_id = service.new_task_id()

    await service.run(TaskRequest(title="t", description="d", max_revisions=5), task_id=task_id)
    await service.resume(task_id, "first directive")
    final = await service.resume(task_id, "second directive")

    assert final.status == "completed"
    snap = await service.graph.aget_state({"configurable": {"thread_id": task_id}})
    assert snap.values.get("user_directives") == ["first directive", "second directive"]


def test_capped_append_drops_oldest_at_cap() -> None:
    """`capped_append` must FIFO-trim, keeping the most-recent items."""

    reducer = capped_append(3)
    assert reducer([], [1, 2]) == [1, 2]
    assert reducer([1, 2], [3]) == [1, 2, 3]
    assert reducer([1, 2, 3], [4]) == [2, 3, 4]
    assert reducer([1, 2, 3, 4], [5, 6]) == [4, 5, 6]
    assert reducer(None, [1]) == [1]
    assert reducer([1], None) == [1]


@pytest.mark.asyncio
async def test_events_field_is_capped() -> None:
    """A pathological run that produces > EVENTS_SOFT_CAP events must trim FIFO."""

    overflow = EVENTS_SOFT_CAP + 50
    bindings = bindings_with_fakes()
    graph = build_agent_room_graph(bindings)
    service = AgentRoomService(graph)
    task_id = service.new_task_id()

    await service.run(TaskRequest(title="t", description="d"), task_id=task_id)
    config = {"configurable": {"thread_id": task_id}}
    await service.graph.aupdate_state(
        config,
        {"events": [Event(type="probe", role=None, payload={"i": i}) for i in range(overflow)]},
    )

    snap = await service.graph.aget_state(config)
    events = snap.values["events"]
    assert len(events) == EVENTS_SOFT_CAP
    last_probe = events[-1]
    assert isinstance(last_probe, Event) or last_probe.get("type") == "probe"
