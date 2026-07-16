"""Run a single task without the CLI. Useful for sanity checks.

Usage:
    ANTHROPIC_API_KEY=... python examples/run_once.py
"""

from __future__ import annotations

import asyncio

from agent_room.config import RoleBindings, load_settings
from agent_room.graph import build_with_sqlite_checkpointer
from agent_room.schemas import TaskRequest
from agent_room.service import AgentRoomService


async def main() -> None:
    settings = load_settings()
    bindings = RoleBindings(settings=settings)
    async with build_with_sqlite_checkpointer(bindings, settings.db_path) as graph:
        service = AgentRoomService(graph)
        req = TaskRequest(
            title="FizzBuzz with tests",
            description=(
                "Implement classic FizzBuzz in Python with a CLI entry point and "
                "pytest-based unit tests covering 1, 3, 5, 15, and 100."
            ),
            max_revisions=2,
        )
        result = await service.run(req)
        print(f"status={result.status} rounds={result.rounds}")
        if result.delivery:
            print("\n--- delivery ---\n", result.delivery)


if __name__ == "__main__":
    asyncio.run(main())
