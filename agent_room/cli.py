"""Typer CLI: `agent-room run|serve|resume|show`."""

from __future__ import annotations

import asyncio

import typer
from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel

from agent_room.config import RoleBindings, load_settings
from agent_room.events import StreamEventFormatter
from agent_room.graph import build_with_sqlite_checkpointer
from agent_room.schemas import TaskRequest, TaskResult
from agent_room.service import AgentRoomService
from agent_room.spec import resolve_spec_source

app = typer.Typer(help="agent-room: multi-role LLM orchestrator on LangGraph.")
console = Console()


@app.callback()
def _main() -> None:
    """Configure observability from env: AGENT_ROOM_TRACE (OTEL), AGENT_ROOM_LOG (structlog)."""
    from agent_room.obs.logging import configure_from_env as configure_logging_from_env
    from agent_room.obs.tracing import configure_from_env as configure_tracing_from_env

    configure_tracing_from_env()
    configure_logging_from_env()


GRAPH_OPTION = typer.Option(
    "full",
    "--graph",
    "-g",
    help="Preset name (full / dev_review / solo) or path to a GraphSpec YAML file.",
)


def _print_result(result: TaskResult) -> None:
    console.print(
        Panel.fit(
            f"[bold]Task[/bold] {result.task_id}  "
            f"[cyan]{result.status}[/cyan]  rounds={result.rounds}"
        )
    )
    if result.plan:
        console.print(Panel(Markdown(result.plan), title="plan", border_style="blue"))
    if result.code:
        console.print(Panel(Markdown(result.code), title="code", border_style="green"))
    if result.review:
        console.print(
            Panel(
                f"[bold]{result.review.decision}[/bold] "
                f"(conf={result.review.confidence:.2f})\n\n{result.review.feedback}",
                title="reviewer",
                border_style="magenta",
            )
        )
    if result.delivery:
        console.print(Panel(Markdown(result.delivery), title="delivery", border_style="yellow"))


@app.command()
def run(
    title: str = typer.Argument(..., help="Short task title"),
    description: str = typer.Option("", "--description", "-d"),
    max_revisions: int = typer.Option(2, "--max-revisions", "-r"),
    stream: bool = typer.Option(False, "--stream/--no-stream", help="Print live events"),
    graph: str = GRAPH_OPTION,
) -> None:
    """Run a task end-to-end against the local SQLite-backed graph."""

    spec = resolve_spec_source(graph)

    async def _go() -> None:
        settings = load_settings()
        bindings = RoleBindings(settings=settings)
        async with build_with_sqlite_checkpointer(
            bindings, settings.db_path, spec=spec
        ) as compiled:
            service = AgentRoomService(compiled)
            req = TaskRequest(
                title=title, description=description or title, max_revisions=max_revisions
            )
            if stream:
                task_id = service.new_task_id()
                formatter = StreamEventFormatter()
                async for raw in service.stream(req, task_id=task_id):
                    formatted = formatter.format(raw)
                    if formatted is None:
                        continue
                    if formatted["type"] == "token":
                        console.print(formatted.get("text", ""), end="", soft_wrap=True)
                    else:
                        console.print(f"\n[dim]{formatted}[/dim]")
                for formatted in formatter.flush():
                    if formatted["type"] == "token":
                        console.print(formatted.get("text", ""), end="", soft_wrap=True)
                    else:
                        console.print(f"\n[dim]{formatted}[/dim]")
                console.print()
                result = await service.snapshot(task_id)
            else:
                result = await service.run(req)
            _print_result(result)

    asyncio.run(_go())


@app.command()
def show(
    task_id: str,
    graph: str = GRAPH_OPTION,
) -> None:
    """Show the latest snapshot for a task."""

    spec = resolve_spec_source(graph)

    async def _go() -> None:
        settings = load_settings()
        bindings = RoleBindings(settings=settings)
        async with build_with_sqlite_checkpointer(
            bindings, settings.db_path, spec=spec
        ) as compiled:
            service = AgentRoomService(compiled)
            result = await service.snapshot(task_id)
            _print_result(result)

    asyncio.run(_go())


@app.command()
def resume(
    task_id: str,
    decision: str = typer.Option(..., "--decision", "-d", help="Direction for the team"),
    graph: str = GRAPH_OPTION,
) -> None:
    """Resume a task that halted on need_user_decision."""

    spec = resolve_spec_source(graph)

    async def _go() -> None:
        settings = load_settings()
        bindings = RoleBindings(settings=settings)
        async with build_with_sqlite_checkpointer(
            bindings, settings.db_path, spec=spec
        ) as compiled:
            service = AgentRoomService(compiled)
            result = await service.resume(task_id, decision)
            _print_result(result)

    asyncio.run(_go())


@app.command()
def serve(
    host: str | None = typer.Option(None, "--host"),
    port: int | None = typer.Option(None, "--port"),
) -> None:
    """Run the FastAPI + SSE server."""

    import uvicorn

    settings = load_settings()
    uvicorn.run(
        "agent_room.server.api:app",
        host=host or settings.host,
        port=port or settings.port,
        reload=False,
    )


if __name__ == "__main__":
    app()
