"""SessionStore (DAO) tests — schema, CRUD, task linking, isolation."""

from __future__ import annotations

import asyncio
import dataclasses
import sqlite3
import time
from pathlib import Path

import pytest

from agent_room.server.sessions import (
    SessionRecord,
    SessionStore,
    new_session_id,
)


@pytest.fixture
async def store() -> SessionStore:
    s = SessionStore()
    await s.initialize(":memory:")
    return s


@pytest.mark.asyncio
async def test_initialize_creates_tables_in_memory() -> None:
    s = SessionStore()
    await s.initialize(":memory:")
    try:
        records = await s.list_all()
        assert records == []
    finally:
        await s.close()


@pytest.mark.asyncio
async def test_create_and_get_round_trip(store: SessionStore) -> None:
    rec = await store.create("My Project")
    assert rec.id.startswith("sess-")
    assert rec.name == "My Project"
    assert rec.created_at > 0

    fetched = await store.get(rec.id)
    assert fetched == rec


@pytest.mark.asyncio
async def test_list_all_returns_newest_first(store: SessionStore) -> None:
    a = await store.create("alpha")
    await asyncio.sleep(0.01)
    b = await store.create("beta")
    listed = await store.list_all()
    assert [r.id for r in listed] == [b.id, a.id]


@pytest.mark.asyncio
async def test_get_missing_returns_none(store: SessionStore) -> None:
    assert await store.get("sess-does-not-exist") is None


@pytest.mark.asyncio
async def test_delete_removes_session(store: SessionStore) -> None:
    rec = await store.create("doomed")
    assert await store.delete(rec.id) is True
    assert await store.get(rec.id) is None
    assert await store.delete(rec.id) is False


@pytest.mark.asyncio
async def test_create_rejects_blank_name(store: SessionStore) -> None:
    with pytest.raises(ValueError):
        await store.create("   ")


@pytest.mark.asyncio
async def test_attach_task_links_to_session(store: SessionStore) -> None:
    rec = await store.create("project")
    ok = await store.attach_task(rec.id, "task-abc")
    assert ok is True
    links = await store.list_tasks(rec.id)
    assert [link.task_id for link in links] == ["task-abc"]


@pytest.mark.asyncio
async def test_attach_task_unknown_session_returns_false(store: SessionStore) -> None:
    assert await store.attach_task("sess-missing", "task-abc") is False


@pytest.mark.asyncio
async def test_attach_task_is_idempotent(store: SessionStore) -> None:
    rec = await store.create("project")
    await store.attach_task(rec.id, "task-1")
    await store.attach_task(rec.id, "task-1")
    links = await store.list_tasks(rec.id)
    assert len(links) == 1


@pytest.mark.asyncio
async def test_list_tasks_orders_newest_first(store: SessionStore) -> None:
    rec = await store.create("project")
    await store.attach_task(rec.id, "task-1")
    await asyncio.sleep(0.01)
    await store.attach_task(rec.id, "task-2")
    links = await store.list_tasks(rec.id)
    assert [link.task_id for link in links] == ["task-2", "task-1"]


@pytest.mark.asyncio
async def test_delete_session_cascades_links(store: SessionStore) -> None:
    rec = await store.create("project")
    await store.attach_task(rec.id, "task-1")
    await store.delete(rec.id)
    links = await store.list_tasks(rec.id)
    assert links == []


@pytest.mark.asyncio
async def test_persists_to_disk_across_reopens(tmp_path: Path) -> None:
    db_path = str(tmp_path / "agent_room.db")
    s1 = SessionStore()
    await s1.initialize(db_path)
    rec = await s1.create("kept")
    await s1.attach_task(rec.id, "task-x")
    await s1.close()

    s2 = SessionStore()
    await s2.initialize(db_path)
    try:
        listed = await s2.list_all()
        assert [r.id for r in listed] == [rec.id]
        links = await s2.list_tasks(rec.id)
        assert [link.task_id for link in links] == ["task-x"]
    finally:
        await s2.close()


@pytest.mark.asyncio
async def test_does_not_collide_with_checkpointer_tables(tmp_path: Path) -> None:
    """SessionStore lives in the same db_path as the checkpointer; check namespacing."""
    db_path = str(tmp_path / "shared.db")
    s = SessionStore()
    await s.initialize(db_path)
    try:
        rec = await s.create("shared")
        assert rec.name == "shared"
    finally:
        await s.close()

    conn = sqlite3.connect(db_path)
    try:
        rows = conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        names = {r[0] for r in rows}
    finally:
        conn.close()

    assert "agent_room_sessions" in names
    assert "agent_room_session_tasks" in names
    for table in ("checkpoints", "writes", "blobs"):
        assert table not in names


def test_new_session_id_is_unique() -> None:
    ids = {new_session_id() for _ in range(50)}
    assert len(ids) == 50
    for sid in ids:
        assert sid.startswith("sess-")


def test_session_record_is_frozen() -> None:
    rec = SessionRecord(id="sess-x", name="n", created_at=time.time())
    with pytest.raises(dataclasses.FrozenInstanceError):
        rec.name = "other"  # type: ignore[misc]
