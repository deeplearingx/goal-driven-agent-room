"""Integration test: full run against AsyncSqliteSaver, then resume."""

from __future__ import annotations

import os
import tempfile

import pytest
from langchain_core.language_models.fake_chat_models import FakeListChatModel

from agent_room.config import RoleBindings
from agent_room.graph import build_with_sqlite_checkpointer
from agent_room.schemas import ReviewerDecision, TaskRequest
from agent_room.service import AgentRoomService
from tests.fakes import FakeReviewerLLM


@pytest.mark.asyncio
async def test_sqlite_checkpointer_persists_and_resumes() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        db_path = os.path.join(tmp, "ar.db")

        bindings = RoleBindings(
            planner=FakeListChatModel(responses=["plan"]),
            developer=FakeListChatModel(responses=["v1", "v2"]),
            reviewer=FakeReviewerLLM(
                responses=["unused"],
                decisions=[
                    ReviewerDecision(
                        decision="need_user_decision",
                        feedback="A or B?",
                        issues=[],
                        confidence=0.5,
                    ),
                    ReviewerDecision(
                        decision="approved",
                        feedback="ok",
                        issues=[],
                        confidence=0.95,
                    ),
                ],
            ),
            delivery=FakeListChatModel(responses=["# Final"]),
        )

        async with build_with_sqlite_checkpointer(bindings, db_path) as graph:
            service = AgentRoomService(graph)
            task_id = service.new_task_id()
            r1 = await service.run(TaskRequest(title="t", description="d"), task_id=task_id)
            assert r1.status == "awaiting_user"

            r2 = await service.resume(task_id, "use A")
            assert r2.status == "completed"
            assert r2.delivery == "# Final"
            assert r2.task_id == task_id

        # Re-open with a fresh graph: snapshot should still be retrievable.
        async with build_with_sqlite_checkpointer(bindings, db_path) as graph2:
            service2 = AgentRoomService(graph2)
            snap = await service2.snapshot(task_id)
            assert snap.status == "completed"
            assert snap.delivery == "# Final"
