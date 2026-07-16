"""v1.x §6.15 — tenant routing at the HTTP layer: `POST /tasks` with different
`tenant_id`s lands in physically separate `agent_room.db` files, and
cross-tenant access to a known `task_id` is rejected.

Uses the *production* lifespan (`bindings=`, `service=None`) so the real
`TenantCheckpointerPool` is exercised — `tests/test_api.py`'s existing tests
all use the injected-`service` test path, which never touches it. Fake LLMs
(`bindings_with_fakes`) keep this offline/deterministic; `AGENT_ROOM_GRAPH=full`
avoids the tool-calling ReAct loop (irrelevant to what's under test here).
"""

from __future__ import annotations

from pathlib import Path

import pytest
from asgi_lifespan import LifespanManager
from httpx import ASGITransport, AsyncClient

from agent_room.server.api import create_app
from tests.fakes import bindings_with_fakes


@pytest.fixture
def _tenancy_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("AGENT_ROOM_DB", str(tmp_path / "agent_room.db"))
    monkeypatch.setenv("AGENT_ROOM_WORKSPACE", str(tmp_path / "workspace"))
    monkeypatch.setenv("AGENT_ROOM_GRAPH", "full")
    monkeypatch.setenv("AGENT_ROOM_MEMORY", "1")
    # Do not inherit a developer machine's optional direct-shell escape hatch.
    monkeypatch.setenv("AGENT_ROOM_SHELL_ALLOWLIST", "")
    monkeypatch.setenv("AGENT_ROOM_ALLOW_DIRECT_SHELL", "0")
    monkeypatch.setenv("AGENT_ROOM_TOOL_MODE", "read_only")
    return tmp_path


@pytest.mark.asyncio
async def test_two_tenants_get_physically_separate_dbs(_tenancy_env: Path) -> None:
    app = create_app(bindings=bindings_with_fakes())
    async with (
        LifespanManager(app),
        AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client,
    ):
        r_a = await client.post(
            "/tasks",
            json={"title": "task A", "description": "d", "tenant_id": "tenant-a"},
        )
        assert r_a.status_code == 200
        r_b = await client.post(
            "/tasks",
            json={"title": "task B", "description": "d", "tenant_id": "tenant-b"},
        )
        assert r_b.status_code == 200

    base_dir = _tenancy_env
    db_a = base_dir / "tenants" / "tenant-a" / "agent_room.db"
    db_b = base_dir / "tenants" / "tenant-b" / "agent_room.db"
    default_db = base_dir / "agent_room.db"
    assert db_a.exists()
    assert db_b.exists()
    # Neither tenant touched the default db_path.
    assert default_db.exists()  # created for the pool's eager default-tenant warm-up
    assert (base_dir / "tenants" / "tenant-a" / "memory").is_dir()
    assert (base_dir / "tenants" / "tenant-b" / "memory").is_dir()


@pytest.mark.asyncio
async def test_default_tenant_task_lands_in_base_db(_tenancy_env: Path) -> None:
    app = create_app(bindings=bindings_with_fakes())
    async with (
        LifespanManager(app),
        AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client,
    ):
        r = await client.post("/tasks", json={"title": "t", "description": "d"})
        assert r.status_code == 200

    base_dir = _tenancy_env
    assert not (base_dir / "tenants").exists()  # no non-default tenant ever used


@pytest.mark.asyncio
async def test_cross_tenant_get_task_is_rejected(_tenancy_env: Path) -> None:
    app = create_app(bindings=bindings_with_fakes())
    async with (
        LifespanManager(app),
        AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client,
    ):
        create = await client.post(
            "/tasks/stream",
            json={"title": "owned by A", "description": "d", "tenant_id": "tenant-a"},
        )
        assert create.status_code == 200
        # Pull the task_id back out of the SSE stream's first frame.
        body = create.text
        assert "task_id" in body
        import re

        m = re.search(r'"task_id":\s*"([^"]+)"', body)
        assert m is not None
        task_id = m.group(1)

        own = await client.get(f"/tasks/{task_id}", params={"tenant_id": "tenant-a"})
        assert own.status_code == 200

        cross = await client.get(f"/tasks/{task_id}", params={"tenant_id": "tenant-b"})
        assert cross.status_code == 404


@pytest.mark.asyncio
async def test_cross_tenant_cancel_is_rejected(_tenancy_env: Path) -> None:
    app = create_app(bindings=bindings_with_fakes())
    async with (
        LifespanManager(app),
        AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client,
    ):
        create = await client.post(
            "/tasks/stream",
            json={"title": "owned by A", "description": "d", "tenant_id": "tenant-a"},
        )
        assert create.status_code == 200
        import re

        m = re.search(r'"task_id":\s*"([^"]+)"', create.text)
        assert m is not None
        task_id = m.group(1)

        cross = await client.post(f"/tasks/{task_id}/cancel", params={"tenant_id": "tenant-b"})
        assert cross.status_code == 404


@pytest.mark.asyncio
async def test_tenant_path_traversal_is_rejected(_tenancy_env: Path) -> None:
    app = create_app(bindings=bindings_with_fakes())
    async with (
        LifespanManager(app),
        AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client,
    ):
        response = await client.post(
            "/tasks",
            json={"title": "escape", "description": "d", "tenant_id": "../outside"},
        )
        assert response.status_code == 422
    assert not (_tenancy_env.parent / "outside").exists()
