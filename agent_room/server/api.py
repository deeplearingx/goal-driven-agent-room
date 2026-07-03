"""FastAPI application factory.

Endpoints:
    POST   /tasks              create + run a task synchronously, returns TaskResult
    POST   /tasks/stream       create + run a task, stream events as SSE
    GET    /tasks/{task_id}    fetch current snapshot
    GET    /tasks/{task_id}/stream  EventSource-friendly GET variant (UI use)
    POST   /tasks/{task_id}/resume  continue an awaiting_user run
    GET    /healthz

Compatibility surface (v1.0, ADR-0011):
    `/api/agent-room/sessions[...]` — thin (id, name, created_at) tag layer
    + task linking. See `agent_room.server.compat_v1`.

In-repo UI (v1.0, ADR-0012):
    `/ui` + `/ui/tasks/{task_id}` — server-rendered Jinja2 pages + a single
    `app.js`. `/` redirects to `/ui`. See `agent_room.server.ui`.
"""

from __future__ import annotations

import json
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from sse_starlette.sse import EventSourceResponse

from agent_room.budget import describe_budget
from agent_room.config import RoleBindings, load_settings
from agent_room.events import StreamEventFormatter
from agent_room.guardrail import GuardrailTripwire, describe_guardrail
from agent_room.memory import FileFtsMemoryProvider
from agent_room.schemas import TaskRequest, TaskResult
from agent_room.server.compat_v1 import build_compat_v1_router
from agent_room.server.react_runtime import (
    build_server_memory,
    build_server_registry,
    build_server_spec,
    describe_tool_envelope,
    load_server_mcp_tools,
    task_workspace,
    workspace_dir,
)
from agent_room.server.runs import RunManager
from agent_room.server.sessions import SessionStore
from agent_room.server.tenancy import (
    DEFAULT_TENANT_ID,
    TenantCheckpointerPool,
    clone_bindings_for_tenant,
)
from agent_room.server.ui import build_ui_router, ui_static_dir
from agent_room.service import AgentRoomService
from agent_room.tools._safety import resolve_within_root


class ResumeBody(BaseModel):
    decision: str


class WorkspaceFile(BaseModel):
    path: str
    size: int


class WorkspaceListing(BaseModel):
    files: list[WorkspaceFile]


class WorkspaceFileContent(BaseModel):
    path: str
    content: str
    truncated: bool
    binary: bool


_WORKSPACE_MAX_BYTES = 256 * 1024
_WORKSPACE_SKIP_DIRS = {"__pycache__", ".git", "node_modules"}

# Presets a client may request per-task via `TaskRequest.graph` (the two
# "modes"). Deliberately NOT "any loadable preset": e.g. `solo`/`dev_review`
# skip the reviewer, so exposing arbitrary preset selection to callers would
# let them bypass review. Operators can still run any preset server-wide via
# AGENT_ROOM_GRAPH; this only bounds what a request body can switch to.
USER_SELECTABLE_GRAPHS = frozenset({"full_react", "goal"})


def _serialize_for_sse(payload: dict[str, Any]) -> str:
    def default(obj: Any) -> Any:
        if hasattr(obj, "model_dump"):
            return obj.model_dump(mode="json")
        if hasattr(obj, "isoformat"):
            return obj.isoformat()
        return str(obj)

    return json.dumps(payload, default=default, ensure_ascii=False)


def _cors_origins() -> list[str]:
    raw = os.getenv("AGENT_ROOM_CORS_ORIGINS", "*").strip()
    if raw == "*" or not raw:
        return ["*"]
    return [origin.strip() for origin in raw.split(",") if origin.strip()]


def pixel_app_dir() -> Path:
    """Built pixel UI (`mulit_agent_web_ui`). Served same-origin at `/app` so the
    whole demo runs from one process. `AGENT_ROOM_PIXEL_DIST` overrides the path;
    if it doesn't exist (frontend not built), the mount is skipped."""
    override = os.getenv("AGENT_ROOM_PIXEL_DIST")
    if override:
        return Path(override)
    return Path(__file__).resolve().parents[2] / "mulit_agent_web_ui" / "dist"


def create_app(
    *,
    bindings: RoleBindings | None = None,
    service: AgentRoomService | None = None,
    session_store: SessionStore | None = None,
) -> FastAPI:
    """Build the FastAPI app.

    Production path: pass `bindings`; lifespan owns an `AsyncSqliteSaver`-backed graph.
    Test path: pass a pre-built `service` (e.g. with `MemorySaver` + fakes); lifespan is a no-op.
    `session_store` (optional) backs the `/api/agent-room/*` compat surface; if omitted,
    the production path opens one against `settings.db_path`, and the test path opens
    one against `:memory:` so compat routes work even without a real DB.
    """

    settings = load_settings()
    # Share one memory provider instance between the bindings (renders the
    # system-prompt block) and the `memory` tool in the registry (writes facts).
    # `budget=None` (default, no AGENT_ROOM_MAX_* env set) keeps every task run
    # unbounded — see agent_room/budget.py. `guardrail` defaults to mode="off"
    # (AGENT_ROOM_GUARDRAIL unset) — see agent_room/guardrail.py.
    bindings = bindings or RoleBindings(
        settings=settings,
        memory=build_server_memory(settings),
        budget=settings.budget(),
        guardrail=settings.guardrail(),
    )
    # Detached task runs + replayable event buffers (SSE resume). Lives for the
    # app's lifetime; runs survive client disconnects so reconnects can resume.
    run_manager = RunManager()

    if service is not None:
        provided_store = session_store

        @asynccontextmanager
        async def lifespan(app: FastAPI) -> AsyncIterator[None]:
            app.state.service = service
            store = provided_store or SessionStore()
            if provided_store is None:
                await store.initialize(":memory:")
            app.state.session_store = store
            try:
                yield
            finally:
                await run_manager.aclose()
                if provided_store is None:
                    await store.close()
    else:

        @asynccontextmanager
        async def lifespan(app: FastAPI) -> AsyncIterator[None]:
            store = session_store or SessionStore()
            owns_store = session_store is None
            if owns_store:
                await store.initialize(settings.db_path)
            app.state.session_store = store
            # Default graph is `full_react`: a tool-using developer confined to a
            # workspace with an allowlisted shell. MCP tools (if
            # AGENT_ROOM_MCP_SERVERS is configured) are loaded once here and
            # reused across every task's registry (and every tenant's) — see
            # react_runtime.load_server_mcp_tools.
            mcp_tools = await load_server_mcp_tools(settings)
            mcp_tool_names = tuple(t.name for t in mcp_tools)
            spec = build_server_spec(settings, mcp_tool_names=mcp_tool_names)
            app.state.spec = spec
            app.state.mcp_tools = mcp_tools
            app.state.mcp_tool_names = mcp_tool_names
            # Per-request graph override (workflow/goal) reuses these built
            # specs by preset name; the default preset is seeded here so the
            # no-override path hits the cache too. See task_service_for.
            app.state.spec_cache = {settings.graph_preset: spec}
            # TenantCheckpointerPool (v1.x §6.15): each tenant lazily gets its
            # own checkpointer + memory provider, physically isolated in its
            # own agent_room.db. tenant_id="default" resolves to
            # settings.db_path unchanged — see agent_room/server/tenancy.py.
            bindings_factory = clone_bindings_for_tenant(
                bindings,
                lambda tenant_dir: build_server_memory(settings, tenant_dir=tenant_dir),
            )
            tenant_pool = TenantCheckpointerPool(bindings_factory, settings.db_path)
            app.state.tenant_pool = tenant_pool
            try:
                default_entry = await tenant_pool.get(DEFAULT_TENANT_ID)
                default_mem = (
                    default_entry.memory
                    if isinstance(default_entry.memory, FileFtsMemoryProvider)
                    else None
                )
                base_registry = build_server_registry(
                    settings, memory_provider=default_mem, mcp_tools=mcp_tools
                )
                app.state.service = AgentRoomService(
                    default_entry.compile_with(spec, base_registry)
                )
                yield
            finally:
                await run_manager.aclose()
                await tenant_pool.aclose()
                if owns_store:
                    await store.close()

    app = FastAPI(title="agent-room", version="0.1.0", lifespan=lifespan)

    # A separate-origin frontend (e.g. the pixel UI) needs CORS. Configurable via
    # AGENT_ROOM_CORS_ORIGINS (comma-separated); defaults to "*" for local dev.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=_cors_origins(),
        allow_methods=["*"],
        allow_headers=["*"],
    )

    def _spec_for(graph: str | None) -> Any:
        """The compiled-graph spec for this request's mode. `None` (no
        override) → the server's default. An allowlisted override → a spec
        built for that preset, cached by name so we don't re-parse the YAML
        every request. Allowlist enforcement happens in the handlers."""
        if graph is None:
            return app.state.spec
        cache: dict[str, Any] = app.state.spec_cache
        if graph not in cache:
            cache[graph] = build_server_spec(
                settings,
                graph_preset=graph,
                mcp_tool_names=getattr(app.state, "mcp_tool_names", ()),
            )
        return cache[graph]

    async def task_service_for(
        task_id: str, tenant_id: str = DEFAULT_TENANT_ID, graph: str | None = None
    ) -> AgentRoomService:
        """A service whose tools are rooted at workspace/<task_id>, backed by
        `tenant_id`'s own (physically isolated, v1.x §6.15) checkpointer +
        memory, compiled for `graph`'s preset (workflow/goal; `None` = server
        default). Falls back to the shared service on the test path (an
        injected service with no tenant_pool)."""
        tenant_pool: TenantCheckpointerPool | None = getattr(app.state, "tenant_pool", None)
        if tenant_pool is None:
            return app.state.service  # type: ignore[no-any-return]
        entry = await tenant_pool.get(tenant_id)
        mem = entry.memory if isinstance(entry.memory, FileFtsMemoryProvider) else None
        registry = build_server_registry(
            settings,
            memory_provider=mem,
            workspace_override=task_workspace(settings, task_id),
            mcp_tools=getattr(app.state, "mcp_tools", None),
        )
        return AgentRoomService(entry.compile_with(_spec_for(graph), registry))

    @app.get("/healthz")
    async def healthz() -> dict[str, Any]:
        return {
            "ok": True,
            "role_bindings": bindings.model_ids(),
            "tool_envelope": describe_tool_envelope(
                settings, mcp_tool_names=getattr(app.state, "mcp_tool_names", ())
            ),
            "budget": describe_budget(bindings.budget),
            "guardrail": describe_guardrail(bindings.guardrail),
        }

    def _check_input_guardrail(req: TaskRequest) -> None:
        """§6.9-3 "input" checkpoint — the cheapest of the 4: rejecting here
        means the graph never even starts. `mode="off"` (default) is a NoOp."""
        try:
            bindings.guardrail.check(f"{req.title}\n{req.description}", checkpoint="input")
        except GuardrailTripwire as exc:
            raise HTTPException(
                status_code=400,
                detail=f"rejected by guardrail: {exc.finding.category} pattern matched",
            ) from exc

    def _validate_graph(req: TaskRequest) -> None:
        """Reject a per-request graph override that isn't an exposed mode.
        `None` (no override) is always fine — it uses the server default."""
        if req.graph is not None and req.graph not in USER_SELECTABLE_GRAPHS:
            raise HTTPException(
                status_code=400,
                detail=f"graph must be one of {sorted(USER_SELECTABLE_GRAPHS)} or omitted, "
                f"got {req.graph!r}",
            )

    async def _check_tenant_owns_task(task_id: str, tenant_id: str) -> None:
        """v1.x §6.15 defense-in-depth: reject a request that names a
        different tenant_id than the one a known task_id was created under.

        This does **not** protect against a caller that deliberately lies
        about its own tenant_id — agent-room has no authentication, so
        nothing here can distinguish "the real tenant A" from an attacker
        claiming to be tenant A (see SECURITY.md). What it *does* catch:
        an application bug/stale UI state that sends the wrong tenant_id
        for a task_id it holds, which would otherwise silently 404 via the
        (correct, physically-separate) checkpointer lookup — this turns
        that into an explicit, auditable rejection instead.
        """
        store: SessionStore = app.state.session_store
        record = await store.get(task_id)
        if record is not None and record.tenant_id != tenant_id:
            raise HTTPException(status_code=404, detail="task not found")

    @app.post("/tasks", response_model=TaskResult)
    async def create_task(req: TaskRequest) -> TaskResult:
        _check_input_guardrail(req)
        _validate_graph(req)
        task_id = AgentRoomService.new_task_id()
        store: SessionStore = app.state.session_store
        await store.create(
            name=req.title.strip() or task_id, session_id=task_id, tenant_id=req.tenant_id
        )
        await store.attach_task(task_id, task_id)
        service = await task_service_for(task_id, req.tenant_id, graph=req.graph)
        return await service.run(req, task_id=task_id)

    @app.post("/tasks/stream")
    async def stream_task(req: TaskRequest) -> EventSourceResponse:
        _check_input_guardrail(req)
        _validate_graph(req)
        task_id = AgentRoomService.new_task_id()
        service = await task_service_for(task_id, req.tenant_id, graph=req.graph)

        # Record a session up front (id == task_id) so the run shows up in the
        # history sidebar and survives a page refresh — the checkpointer already
        # persists the task state; without this row the UI has no list to fetch.
        store: SessionStore = app.state.session_store
        await store.create(
            name=req.title.strip() or task_id, session_id=task_id, tenant_id=req.tenant_id
        )
        await store.attach_task(task_id, task_id)

        async def produce() -> AsyncIterator[dict[str, Any]]:
            formatter = StreamEventFormatter()
            yield {"event": "task_started", "data": _serialize_for_sse({"task_id": task_id})}
            async for raw in service.stream(req, task_id=task_id):
                formatted = formatter.format(raw)
                if formatted is not None:
                    yield {"event": formatted["type"], "data": _serialize_for_sse(formatted)}
            for formatted in formatter.flush():
                yield {"event": formatted["type"], "data": _serialize_for_sse(formatted)}
            snapshot = await service.snapshot(task_id)
            yield {
                "event": "task_finished",
                "data": _serialize_for_sse(snapshot.model_dump(mode="json")),
            }

        # Detached run: the background task keeps producing even if this client
        # disconnects, so the task isn't lost and a reconnect can resume.
        run_manager.start(task_id, produce)
        return EventSourceResponse(
            run_manager.subscribe(task_id, -1), ping=settings.sse_ping_seconds
        )

    @app.get("/tasks/{task_id}/events")
    async def resume_stream(
        task_id: str, request: Request, tenant_id: str = Query(DEFAULT_TENANT_ID)
    ) -> EventSourceResponse:
        """Reconnect to a live run, replaying frames after `Last-Event-ID` (or the
        `from` query). 409 when the run is gone (finished+evicted / server
        restarted) so the client falls back to snapshot recovery."""
        await _check_tenant_owns_task(task_id, tenant_id)
        if not run_manager.has(task_id):
            raise HTTPException(status_code=409, detail="run not resumable")
        header = request.headers.get("last-event-id")
        raw_from = header if header is not None else request.query_params.get("from", "-1")
        try:
            last = int(raw_from)
        except ValueError:
            last = -1
        return EventSourceResponse(
            run_manager.subscribe(task_id, last), ping=settings.sse_ping_seconds
        )

    @app.post("/tasks/{task_id}/cancel")
    async def cancel_task(
        task_id: str, tenant_id: str = Query(DEFAULT_TENANT_ID)
    ) -> dict[str, Any]:
        """Stop a running task. The detached run is cancelled (which halts the
        graph) and a terminal frame is emitted to any live subscriber."""
        await _check_tenant_owns_task(task_id, tenant_id)
        cancelled = await run_manager.cancel(task_id)
        if not cancelled:
            raise HTTPException(status_code=409, detail="no running task to cancel")
        return {"task_id": task_id, "cancelled": True}

    @app.get("/tasks/{task_id}", response_model=TaskResult)
    async def get_task(task_id: str, tenant_id: str = Query(DEFAULT_TENANT_ID)) -> TaskResult:
        await _check_tenant_owns_task(task_id, tenant_id)
        service = await task_service_for(task_id, tenant_id)
        result = await service.snapshot(task_id)
        if not result.artifacts and not result.events:
            # Empty snapshot 404s only for a genuinely unknown task_id. A known
            # session with no artifacts yet is a still-running / interrupted run —
            # return it (status reflects 'running') so the UI shows it gracefully
            # instead of a "connection lost" error when the user opens it.
            store: SessionStore = app.state.session_store
            if await store.get(task_id) is None:
                raise HTTPException(status_code=404, detail="task not found")
        return result

    @app.post("/tasks/{task_id}/resume", response_model=TaskResult)
    async def resume_task(
        task_id: str, body: ResumeBody, tenant_id: str = Query(DEFAULT_TENANT_ID)
    ) -> TaskResult:
        # Resume may re-run tools, so use the task's own workspace-rooted graph.
        await _check_tenant_owns_task(task_id, tenant_id)
        service = await task_service_for(task_id, tenant_id)
        return await service.resume(task_id, body.decision)

    # --- Generated files: surface what the developer wrote to the sandbox so the
    # UI can show the real artifacts (and preview generated HTML). Read-only and
    # confined to the workspace via resolve_within_root. `task_id` scopes to that
    # task's subdir so the browser shows only its files (no cross-task mixing).
    base_ws = workspace_dir(settings)

    def _files_root(task_id: str | None) -> Path:
        return task_workspace(settings, task_id) if task_id else base_ws

    @app.get("/workspace/files", response_model=WorkspaceListing)
    async def list_workspace_files(task_id: str | None = Query(None)) -> WorkspaceListing:
        root = _files_root(task_id)
        files: list[WorkspaceFile] = []
        for p in sorted(root.rglob("*")):
            if not p.is_file():
                continue
            rel = p.relative_to(root)
            if any(part in _WORKSPACE_SKIP_DIRS or part.startswith(".") for part in rel.parts):
                continue
            files.append(WorkspaceFile(path=str(rel), size=p.stat().st_size))
        return WorkspaceListing(files=files)

    @app.get("/workspace/file", response_model=WorkspaceFileContent)
    async def read_workspace_file(
        path: str = Query(...), task_id: str | None = Query(None)
    ) -> WorkspaceFileContent:
        try:
            target = resolve_within_root(_files_root(task_id), path)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        if not target.is_file():
            raise HTTPException(status_code=404, detail="file not found")
        raw = target.read_bytes()
        truncated = len(raw) > _WORKSPACE_MAX_BYTES
        chunk = raw[:_WORKSPACE_MAX_BYTES]
        try:
            text = chunk.decode("utf-8")
            binary = False
        except UnicodeDecodeError:
            text = ""
            binary = True
        return WorkspaceFileContent(path=path, content=text, truncated=truncated, binary=binary)

    app.include_router(build_compat_v1_router(app))
    app.include_router(build_ui_router(app))
    app.mount("/ui/static", StaticFiles(directory=str(ui_static_dir())), name="ui-static")

    # Pixel React SPA, served same-origin so `/tasks/stream` etc. need no CORS.
    # Mounted only when the frontend has been built (`npm run build`); absent in
    # a bare checkout / CI, so the rest of the API still works.
    pixel_dist = pixel_app_dir()
    pixel_built = (pixel_dist / "index.html").is_file()
    if pixel_built:
        app.mount("/app", StaticFiles(directory=str(pixel_dist), html=True), name="pixel-app")

    # Serve generated files raw so the UI can render generated HTML in a
    # sandboxed iframe (assets resolve relatively). The iframe must use
    # `sandbox` without allow-same-origin so generated JS can't touch the app.
    app.mount("/workspace/preview", StaticFiles(directory=str(base_ws)), name="workspace-preview")

    @app.get("/", include_in_schema=False)
    async def root() -> RedirectResponse:
        # Prefer the pixel SPA when it's built; otherwise the in-repo thin UI.
        return RedirectResponse(url="/app/" if pixel_built else "/ui", status_code=307)

    return app


app = create_app()
