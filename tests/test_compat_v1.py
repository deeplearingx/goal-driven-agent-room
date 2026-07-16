"""`/api/agent-room/*` compat router tests (ADR-0011)."""

from __future__ import annotations

import pytest
from asgi_lifespan import LifespanManager
from httpx import ASGITransport, AsyncClient

from agent_room.graph import build_agent_room_graph
from agent_room.schemas import ReviewerDecision
from agent_room.server.api import create_app
from agent_room.service import AgentRoomService
from tests.fakes import bindings_with_fakes

BASE = "/api/agent-room"


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
        or [
            ReviewerDecision(decision="approved", feedback="lgtm", confidence=0.9),
        ],
        delivery_response=delivery,
    )
    graph = build_agent_room_graph(bindings)
    return AgentRoomService(graph)


@pytest.mark.asyncio
async def test_create_and_list_sessions() -> None:
    app = create_app(service=_make_service())
    async with (
        LifespanManager(app),
        AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client,
    ):
        empty = await client.get(f"{BASE}/sessions")
        assert empty.status_code == 200
        assert empty.json() == {"sessions": []}

        created = await client.post(f"{BASE}/sessions", json={"name": "demo"})
        assert created.status_code == 201, created.text
        body = created.json()
        assert body["name"] == "demo"
        assert body["id"].startswith("sess-")
        assert body["created_at"] > 0

        listed = await client.get(f"{BASE}/sessions")
        assert listed.status_code == 200
        sessions = listed.json()["sessions"]
        assert [s["id"] for s in sessions] == [body["id"]]


@pytest.mark.asyncio
async def test_get_session_404_for_unknown() -> None:
    app = create_app(service=_make_service())
    async with (
        LifespanManager(app),
        AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client,
    ):
        r = await client.get(f"{BASE}/sessions/sess-missing")
        assert r.status_code == 404


@pytest.mark.asyncio
async def test_create_rejects_empty_name() -> None:
    app = create_app(service=_make_service())
    async with (
        LifespanManager(app),
        AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client,
    ):
        r = await client.post(f"{BASE}/sessions", json={"name": ""})
        assert r.status_code == 422


@pytest.mark.asyncio
async def test_delete_session_then_404() -> None:
    app = create_app(service=_make_service())
    async with (
        LifespanManager(app),
        AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client,
    ):
        sid = (await client.post(f"{BASE}/sessions", json={"name": "x"})).json()["id"]
        r1 = await client.delete(f"{BASE}/sessions/{sid}")
        assert r1.status_code == 204
        r2 = await client.get(f"{BASE}/sessions/{sid}")
        assert r2.status_code == 404
        r3 = await client.delete(f"{BASE}/sessions/{sid}")
        assert r3.status_code == 404


@pytest.mark.asyncio
async def test_list_tasks_empty_for_new_session() -> None:
    app = create_app(service=_make_service())
    async with (
        LifespanManager(app),
        AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client,
    ):
        sid = (await client.post(f"{BASE}/sessions", json={"name": "x"})).json()["id"]
        r = await client.get(f"{BASE}/sessions/{sid}/tasks")
        assert r.status_code == 200
        assert r.json() == {"session_id": sid, "task_ids": []}


@pytest.mark.asyncio
async def test_list_tasks_404_for_unknown_session() -> None:
    app = create_app(service=_make_service())
    async with (
        LifespanManager(app),
        AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client,
    ):
        r = await client.get(f"{BASE}/sessions/sess-missing/tasks")
        assert r.status_code == 404


@pytest.mark.asyncio
async def test_create_task_under_session_runs_to_completion() -> None:
    app = create_app(service=_make_service(delivery="# Done"))
    async with (
        LifespanManager(app),
        AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client,
    ):
        sid = (await client.post(f"{BASE}/sessions", json={"name": "x"})).json()["id"]
        r = await client.post(
            f"{BASE}/sessions/{sid}/tasks",
            json={"title": "demo", "description": "hello"},
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["status"] == "completed"
        assert body["delivery"] == "# Done"
        task_id = body["task_id"]

        listed = await client.get(f"{BASE}/sessions/{sid}/tasks")
        assert listed.json() == {"session_id": sid, "task_ids": [task_id]}


@pytest.mark.asyncio
async def test_create_task_under_unknown_session_404() -> None:
    app = create_app(service=_make_service())
    async with (
        LifespanManager(app),
        AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client,
    ):
        r = await client.post(
            f"{BASE}/sessions/sess-missing/tasks",
            json={"title": "x", "description": "y"},
        )
        assert r.status_code == 404


@pytest.mark.asyncio
async def test_resume_session_task() -> None:
    service = _make_service(
        code_responses=["v1", "v2"],
        decisions=[
            ReviewerDecision(decision="need_user_decision", feedback="A or B?"),
            ReviewerDecision(decision="approved", feedback="ok", confidence=0.95),
        ],
        delivery="# Resumed",
    )
    app = create_app(service=service)
    async with (
        LifespanManager(app),
        AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client,
    ):
        sid = (await client.post(f"{BASE}/sessions", json={"name": "x"})).json()["id"]
        r1 = await client.post(
            f"{BASE}/sessions/{sid}/tasks",
            json={"title": "t", "description": "d"},
        )
        assert r1.status_code == 200
        body1 = r1.json()
        assert body1["status"] == "awaiting_user"
        task_id = body1["task_id"]

        r2 = await client.post(
            f"{BASE}/sessions/{sid}/tasks/{task_id}/resume",
            json={"decision": "use A"},
        )
        assert r2.status_code == 200
        assert r2.json()["status"] == "completed"


@pytest.mark.asyncio
async def test_resume_rejects_blank_decision() -> None:
    app = create_app(service=_make_service())
    async with (
        LifespanManager(app),
        AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client,
    ):
        sid = (await client.post(f"{BASE}/sessions", json={"name": "x"})).json()["id"]
        r = await client.post(
            f"{BASE}/sessions/{sid}/tasks/task-anything/resume",
            json={"decision": "   "},
        )
        assert r.status_code == 422


@pytest.mark.asyncio
async def test_resume_unknown_session_404() -> None:
    app = create_app(service=_make_service())
    async with (
        LifespanManager(app),
        AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client,
    ):
        r = await client.post(
            f"{BASE}/sessions/sess-missing/tasks/task-anything/resume",
            json={"decision": "use A"},
        )
        assert r.status_code == 404


@pytest.mark.asyncio
async def test_main_task_routes_unaffected() -> None:
    """Ensure the original /tasks routes still work after compat router mounted."""
    app = create_app(service=_make_service(delivery="# OK"))
    async with (
        LifespanManager(app),
        AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client,
    ):
        r = await client.post("/tasks", json={"title": "x", "description": "y"})
        assert r.status_code == 200
        assert r.json()["status"] == "completed"
        assert r.json()["delivery"] == "# OK"
