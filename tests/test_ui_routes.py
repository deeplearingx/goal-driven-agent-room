"""Tests for the in-repo thin UI routes (ADR-0012)."""

from __future__ import annotations

import json

import pytest
from asgi_lifespan import LifespanManager
from httpx import ASGITransport, AsyncClient

from agent_room.graph import build_agent_room_graph
from agent_room.schemas import ReviewerDecision
from agent_room.server.api import create_app
from agent_room.service import AgentRoomService
from tests.fakes import bindings_with_fakes


def _make_service(
    *,
    decisions: list[ReviewerDecision] | None = None,
    code_responses: list[str] | None = None,
    delivery: str = "# Final",
) -> AgentRoomService:
    bindings = bindings_with_fakes(
        plan_response="step 1\nstep 2",
        code_responses=code_responses or ["print('hi')"],
        decisions=decisions
        or [ReviewerDecision(decision="approved", feedback="lgtm", confidence=0.9)],
        delivery_response=delivery,
    )
    graph = build_agent_room_graph(bindings)
    return AgentRoomService(graph)


@pytest.mark.asyncio
async def test_root_redirects_to_ui(tmp_path, monkeypatch) -> None:
    # Pin the pixel SPA absent so this exercises the /ui fallback regardless of
    # whether the frontend happens to be built locally (see test_api.py for the
    # built-/app/ case).
    monkeypatch.setenv("AGENT_ROOM_PIXEL_DIST", str(tmp_path / "nonexistent"))
    app = create_app(service=_make_service())
    async with (
        LifespanManager(app),
        AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client,
    ):
        r = await client.get("/", follow_redirects=False)
        assert r.status_code == 307
        assert r.headers["location"] == "/ui"


@pytest.mark.asyncio
async def test_ui_index_renders_html() -> None:
    app = create_app(service=_make_service())
    async with (
        LifespanManager(app),
        AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client,
    ):
        r = await client.get("/ui")
        assert r.status_code == 200
        assert r.headers["content-type"].startswith("text/html")
        body = r.text
        assert "<title>agent-room — tasks</title>" in body
        assert 'id="new-task"' in body
        assert "/ui/static/app.js" in body


@pytest.mark.asyncio
async def test_ui_task_page_embeds_task_id() -> None:
    app = create_app(service=_make_service())
    async with (
        LifespanManager(app),
        AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client,
    ):
        r = await client.get("/ui/tasks/task-abc123")
        assert r.status_code == 200
        body = r.text
        assert "task-abc123" in body
        assert 'window.AGENT_ROOM_TASK_ID = "task-abc123"' in body
        assert 'id="status"' in body
        assert 'id="resume-card"' in body


@pytest.mark.asyncio
async def test_ui_static_app_js_served() -> None:
    app = create_app(service=_make_service())
    async with (
        LifespanManager(app),
        AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client,
    ):
        r = await client.get("/ui/static/app.js")
        assert r.status_code == 200
        assert "javascript" in r.headers["content-type"]
        body = r.text
        assert "EventSource" in body
        assert "deriveStatus" in body


@pytest.mark.asyncio
async def test_get_stream_replays_completed_task() -> None:
    app = create_app(service=_make_service(delivery="# Done"))
    async with (
        LifespanManager(app),
        AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client,
    ):
        run = await client.post("/tasks", json={"title": "demo", "description": "hello"})
        assert run.status_code == 200, run.text
        task_id = run.json()["task_id"]

        async with client.stream("GET", f"/tasks/{task_id}/stream") as response:
            assert response.status_code == 200
            events: list[tuple[str, dict]] = []
            event_name: str | None = None
            async for line in response.aiter_lines():
                if line.startswith("event:"):
                    event_name = line.split(":", 1)[1].strip()
                elif line.startswith("data:") and event_name:
                    payload = json.loads(line.split(":", 1)[1].strip())
                    events.append((event_name, payload))
                    event_name = None

        kinds = [name for name, _ in events]
        assert kinds[0] == "task_started"
        assert kinds[-1] == "task_finished"
        final = events[-1][1]
        assert final["status"] == "completed"
        assert final["delivery"] == "# Done"


@pytest.mark.asyncio
async def test_get_stream_unknown_task_404() -> None:
    app = create_app(service=_make_service())
    async with (
        LifespanManager(app),
        AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client,
    ):
        r = await client.get("/tasks/task-does-not-exist/stream")
        assert r.status_code == 404
