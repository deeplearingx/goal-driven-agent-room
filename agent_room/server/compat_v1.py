"""`/api/agent-room/*` compatibility routes for the Vue UI (ADR-0011).

Thin shell over the task-rooted core. Sessions are tags: `(id, name,
created_at)` plus a `task_id` link table. No lifecycle, no metrics, no
config. See [docs/adr/0011-vue-ui-state-mapping.md](../../docs/adr/0011-vue-ui-state-mapping.md).
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, FastAPI, HTTPException
from pydantic import BaseModel, Field

from agent_room.schemas import TaskRequest, TaskResult
from agent_room.server.sessions import SessionRecord, SessionStore
from agent_room.service import AgentRoomService


class CreateSessionBody(BaseModel):
    name: str = Field(min_length=1, max_length=200)


class SessionPayload(BaseModel):
    id: str
    name: str
    created_at: float
    # Real task status (the pixel UI's history list needs it). None when the
    # session has no backing task (e.g. created via POST /sessions standalone).
    status: str | None = None

    @classmethod
    def from_record(cls, rec: SessionRecord, *, status: str | None = None) -> SessionPayload:
        return cls(id=rec.id, name=rec.name, created_at=rec.created_at, status=status)


class SessionListPayload(BaseModel):
    sessions: list[SessionPayload]


class SessionTaskListPayload(BaseModel):
    session_id: str
    task_ids: list[str]


def _store(app: FastAPI) -> SessionStore:
    store = getattr(app.state, "session_store", None)
    if store is None:
        raise HTTPException(status_code=503, detail="session store not initialized")
    assert isinstance(store, SessionStore)
    return store


def _service(app: FastAPI) -> AgentRoomService:
    service = getattr(app.state, "service", None)
    if service is None:
        raise HTTPException(status_code=503, detail="service not initialized")
    assert isinstance(service, AgentRoomService)
    return service


def build_compat_v1_router(app: FastAPI) -> APIRouter:
    """Build the `/api/agent-room/*` router. App is captured for state lookup."""

    router = APIRouter(prefix="/api/agent-room", tags=["compat-v1"])

    @router.get("/sessions", response_model=SessionListPayload)
    async def list_sessions() -> SessionListPayload:
        records = await _store(app).list_all()
        service = _service(app)
        payloads: list[SessionPayload] = []
        for r in records:
            # session id == task id (see stream_task). Resolve the real status
            # from the checkpointer; an empty snapshot means no backing task.
            snap = await service.snapshot(r.id)
            status = snap.status if (snap.artifacts or snap.events) else None
            payloads.append(SessionPayload.from_record(r, status=status))
        return SessionListPayload(sessions=payloads)

    @router.post("/sessions", response_model=SessionPayload, status_code=201)
    async def create_session(body: CreateSessionBody) -> SessionPayload:
        try:
            record = await _store(app).create(body.name)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return SessionPayload.from_record(record)

    @router.get("/sessions/{session_id}", response_model=SessionPayload)
    async def get_session(session_id: str) -> SessionPayload:
        record = await _store(app).get(session_id)
        if record is None:
            raise HTTPException(status_code=404, detail="session not found")
        return SessionPayload.from_record(record)

    @router.delete("/sessions/{session_id}", status_code=204)
    async def delete_session(session_id: str) -> None:
        removed = await _store(app).delete(session_id)
        if not removed:
            raise HTTPException(status_code=404, detail="session not found")

    @router.get(
        "/sessions/{session_id}/tasks",
        response_model=SessionTaskListPayload,
    )
    async def list_session_tasks(session_id: str) -> SessionTaskListPayload:
        store = _store(app)
        if await store.get(session_id) is None:
            raise HTTPException(status_code=404, detail="session not found")
        links = await store.list_tasks(session_id)
        return SessionTaskListPayload(
            session_id=session_id,
            task_ids=[link.task_id for link in links],
        )

    @router.post(
        "/sessions/{session_id}/tasks",
        response_model=TaskResult,
    )
    async def create_session_task(session_id: str, req: TaskRequest) -> TaskResult:
        store = _store(app)
        service = _service(app)
        if await store.get(session_id) is None:
            raise HTTPException(status_code=404, detail="session not found")
        task_id = service.new_task_id()
        await store.attach_task(session_id, task_id)
        return await service.run(req, task_id=task_id)

    @router.post(
        "/sessions/{session_id}/tasks/{task_id}/resume",
        response_model=TaskResult,
    )
    async def resume_session_task(
        session_id: str,
        task_id: str,
        body: dict[str, Any],
    ) -> TaskResult:
        store = _store(app)
        service = _service(app)
        if await store.get(session_id) is None:
            raise HTTPException(status_code=404, detail="session not found")
        decision = body.get("decision")
        if not isinstance(decision, str) or not decision.strip():
            raise HTTPException(status_code=422, detail="decision must be a non-empty string")
        return await service.resume(task_id, decision)

    return router
