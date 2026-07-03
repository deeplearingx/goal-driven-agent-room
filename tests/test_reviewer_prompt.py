"""Regression tests for the reviewer's prompt — F2 from 2026-06-11 smoke run.

The reviewer must:
  1. See `state.user_directives` after a `need_user_decision` resume — without
     this it cannot verify whether the developer applied the user's answer.
     This was a real bug found by the live smoke run; before the fix, only the
     developer saw directives.
  2. See the task title + description, not just plan + code, so it has enough
     context to judge whether ambiguity is real or already resolved.

Driven by `FakeStructured.captured_prompts` (added alongside this test).
"""

from __future__ import annotations

import pytest

from agent_room.graph import build_agent_room_graph
from agent_room.schemas import ReviewerDecision, TaskRequest
from agent_room.service import AgentRoomService
from tests.fakes import bindings_with_fakes


@pytest.mark.asyncio
async def test_reviewer_prompt_includes_user_directives_after_resume() -> None:
    bindings = bindings_with_fakes(
        code_responses=["v1 code", "v2 code"],
        decisions=[
            ReviewerDecision(decision="need_user_decision", feedback="retry on 4xx?"),
            ReviewerDecision(decision="approved", feedback="ok", confidence=0.95),
        ],
    )
    structured = bindings.reviewer.with_structured_output(ReviewerDecision)  # type: ignore[union-attr]

    graph = build_agent_room_graph(bindings)
    service = AgentRoomService(graph)
    task_id = service.new_task_id()

    await service.run(TaskRequest(title="t", description="d"), task_id=task_id)
    await service.resume(task_id, "retry only on 5xx and 429")

    second_review_prompt = structured.captured_prompts[-1]  # type: ignore[attr-defined]
    assert "User directives" in second_review_prompt
    assert "retry only on 5xx and 429" in second_review_prompt


@pytest.mark.asyncio
async def test_reviewer_prompt_includes_title_and_description() -> None:
    bindings = bindings_with_fakes(
        decisions=[ReviewerDecision(decision="approved", feedback="ok")],
    )
    structured = bindings.reviewer.with_structured_output(ReviewerDecision)  # type: ignore[union-attr]

    graph = build_agent_room_graph(bindings)
    service = AgentRoomService(graph)
    task_id = service.new_task_id()
    await service.run(
        TaskRequest(title="MY-TITLE", description="DESCR-LINE"),
        task_id=task_id,
    )

    prompt = structured.captured_prompts[0]  # type: ignore[attr-defined]
    assert "MY-TITLE" in prompt
    assert "DESCR-LINE" in prompt


@pytest.mark.asyncio
async def test_reviewer_prompt_omits_directives_section_when_none() -> None:
    """No empty section header when there are no directives — keeps prompt clean.

    The reviewer SYSTEM prompt mentions "User directives" in describing the
    `need_user_decision` workflow, so we look specifically for the section
    header marker we add to the HUMAN message.
    """

    bindings = bindings_with_fakes(
        decisions=[ReviewerDecision(decision="approved", feedback="ok")],
    )
    structured = bindings.reviewer.with_structured_output(ReviewerDecision)  # type: ignore[union-attr]

    graph = build_agent_room_graph(bindings)
    service = AgentRoomService(graph)
    task_id = service.new_task_id()
    await service.run(TaskRequest(title="t", description="d"), task_id=task_id)

    prompt = structured.captured_prompts[0]  # type: ignore[attr-defined]
    assert "# User directives" not in prompt
