"""Stream a task and print live events.

Usage:
    ANTHROPIC_API_KEY=... python examples/streaming.py
"""

from __future__ import annotations

import asyncio

from rich.console import Console

from agent_room.config import RoleBindings, load_settings
from agent_room.events import format_stream_event
from agent_room.graph import build_with_sqlite_checkpointer
from agent_room.schemas import TaskRequest
from agent_room.service import AgentRoomService

console = Console()


async def main() -> None:
    settings = load_settings()
    bindings = RoleBindings(settings=settings)

    async with build_with_sqlite_checkpointer(bindings, settings.db_path) as graph:
        service = AgentRoomService(graph)
        task_id = service.new_task_id()
        req = TaskRequest(
            title="Streaming demo",
            description="Write a Python function `fibonacci(n)` and 3 unit tests.",
            max_revisions=1,
        )

        console.rule(f"[bold cyan]task {task_id}")

        async for raw in service.stream(req, task_id=task_id):
            formatted = format_stream_event(raw)
            if formatted is None:
                continue
            t = formatted["type"]
            if t == "token":
                console.print(formatted.get("text", ""), end="", soft_wrap=True)
            elif t == "node_start":
                console.print(f"\n[bold blue]▶ {formatted['role']}[/]")
            elif t == "node_end":
                console.print(f"\n[bold green]✓ {formatted['role']}[/] {formatted.get('summary')}")

        result = await service.snapshot(task_id)
        console.rule(f"[bold]done — status={result.status} rounds={result.rounds}")
        if result.delivery:
            console.print(result.delivery)


if __name__ == "__main__":
    asyncio.run(main())
