"""Demonstrate the human-in-the-loop pause/resume flow.

Drives a fake LLM stack so the example runs offline, no API key needed.
The shape of the calls (`run` → halts on awaiting_user → `resume`) is identical
to a real run.

Usage:
    python examples/resume_after_user_decision.py
"""

from __future__ import annotations

import asyncio
import os
import tempfile

from langchain_core.language_models.fake_chat_models import FakeListChatModel
from rich.console import Console

from agent_room.config import RoleBindings
from agent_room.graph import build_with_sqlite_checkpointer
from agent_room.schemas import ReviewerDecision, TaskRequest
from agent_room.service import AgentRoomService
from tests.fakes import FakeReviewerLLM

console = Console()


async def main() -> None:
    bindings = RoleBindings(
        planner=FakeListChatModel(responses=["1. design API\n2. implement\n3. test"]),
        developer=FakeListChatModel(
            responses=[
                "def add(a, b): return a + b",
                "def add(a: int, b: int) -> int: return a + b",
            ]
        ),
        reviewer=FakeReviewerLLM(
            responses=["unused"],
            decisions=[
                ReviewerDecision(
                    decision="need_user_decision",
                    feedback="Should we use type hints? PEP 484 vs Python 2 compat?",
                    issues=[],
                    confidence=0.5,
                ),
                ReviewerDecision(
                    decision="approved",
                    feedback="Type hints look good.",
                    issues=[],
                    confidence=0.9,
                ),
            ],
        ),
        delivery=FakeListChatModel(responses=["# Delivered\n\n`add(a, b) -> int`"]),
    )

    with tempfile.TemporaryDirectory() as tmp:
        db_path = os.path.join(tmp, "demo.db")

        async with build_with_sqlite_checkpointer(bindings, db_path) as graph:
            service = AgentRoomService(graph)
            task_id = service.new_task_id()

            console.rule("[bold cyan]Round 1 — run until reviewer asks for guidance")
            r1 = await service.run(
                TaskRequest(title="add()", description="Tiny add helper"),
                task_id=task_id,
            )
            console.print(f"[yellow]status={r1.status}[/]  rounds={r1.rounds}")
            assert r1.status == "awaiting_user"
            assert r1.review is not None
            console.print(f"[bold]Reviewer asks:[/] {r1.review.feedback}")

            console.rule("[bold cyan]Round 2 — user decides, resume")
            user_call = "Use Python 3 type hints (PEP 484)."
            console.print(f"[bold]User says:[/] {user_call}")
            r2 = await service.resume(task_id, user_call)
            console.print(f"[green]status={r2.status}[/]  rounds={r2.rounds}")
            assert r2.status == "completed"
            console.print()
            console.print(r2.delivery or "")


if __name__ == "__main__":
    asyncio.run(main())
