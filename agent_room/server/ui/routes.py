"""HTML routes for the in-repo thin UI (ADR-0012).

Two pages plus one GET stream variant:
    GET /ui                  task list + new-task form
    GET /ui/tasks/{task_id}  task detail + live SSE log + resume form
    GET /tasks/{task_id}/stream  EventSource-friendly GET variant of POST /tasks/stream
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path
from typing import TYPE_CHECKING, Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sse_starlette.sse import EventSourceResponse

from agent_room.events import format_stream_event

if TYPE_CHECKING:
    from fastapi import FastAPI

    from agent_room.service import AgentRoomService

_UI_DIR = Path(__file__).resolve().parent
_TEMPLATES_DIR = _UI_DIR / "templates"
_STATIC_DIR = _UI_DIR / "static"


def ui_static_dir() -> Path:
    return _STATIC_DIR


def build_ui_router(app: FastAPI) -> APIRouter:
    """HTML pages + a GET-shaped stream that EventSource() can subscribe to."""

    templates = Jinja2Templates(directory=str(_TEMPLATES_DIR))
    router = APIRouter()

    @router.get("/ui", response_class=HTMLResponse)
    async def ui_index(request: Request) -> HTMLResponse:
        return templates.TemplateResponse(request, "index.html", {})

    @router.get("/ui/tasks/{task_id}", response_class=HTMLResponse)
    async def ui_task(request: Request, task_id: str) -> HTMLResponse:
        return templates.TemplateResponse(request, "task.html", {"task_id": task_id})

    @router.get("/tasks/{task_id}/stream")
    async def stream_task_get(task_id: str) -> EventSourceResponse:
        """EventSource-friendly variant of POST /tasks/stream.

        EventSource() can only issue GETs. We re-attach to an existing run by
        replaying its current `events` list, then poll the snapshot until the
        task is no longer `running` (LangGraph events stream isn't seekable;
        replay + tail is the simplest correct shape for v1.0).
        """

        service: AgentRoomService = app.state.service
        snapshot = await service.snapshot(task_id)
        if not snapshot.events and not snapshot.artifacts:
            raise HTTPException(status_code=404, detail="task not found")

        async def gen() -> AsyncIterator[dict[str, Any]]:
            yield {
                "event": "task_started",
                "data": _dump({"task_id": task_id}),
            }
            for ev in snapshot.events:
                payload = format_stream_event(
                    {"event": "on_custom_event", "name": ev.type, "data": ev.payload}
                )
                yield {
                    "event": ev.type,
                    "data": _dump(payload or {"type": ev.type, **ev.payload}),
                }
            final = await service.snapshot(task_id)
            yield {
                "event": "task_finished",
                "data": _dump(final.model_dump(mode="json")),
            }

        return EventSourceResponse(gen())

    return router


def _dump(payload: dict[str, Any]) -> str:
    import json

    def default(obj: Any) -> Any:
        if hasattr(obj, "model_dump"):
            return obj.model_dump(mode="json")
        if hasattr(obj, "isoformat"):
            return obj.isoformat()
        return str(obj)

    return json.dumps(payload, default=default, ensure_ascii=False)
