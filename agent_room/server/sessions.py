"""Session DAO — thin (id, name, created_at) tag layer over task_id (ADR-0011)."""

from __future__ import annotations

import contextlib
import time
import uuid
from dataclasses import dataclass

import aiosqlite

_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS agent_room_sessions (
    id          TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    created_at  REAL NOT NULL,
    tenant_id   TEXT NOT NULL DEFAULT 'default'
);
CREATE TABLE IF NOT EXISTS agent_room_session_tasks (
    session_id  TEXT NOT NULL,
    task_id     TEXT NOT NULL,
    attached_at REAL NOT NULL,
    PRIMARY KEY (session_id, task_id),
    FOREIGN KEY (session_id) REFERENCES agent_room_sessions(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_agent_room_session_tasks_task
    ON agent_room_session_tasks(task_id);
CREATE INDEX IF NOT EXISTS idx_agent_room_session_tasks_session_attached
    ON agent_room_session_tasks(session_id, attached_at);
"""

# Must run after the tenant_id migration below: on a pre-tenant_id db, `CREATE
# TABLE IF NOT EXISTS` above is a no-op (table already exists without the
# column), so an index on that column here would fail with "no such column"
# until the ALTER TABLE has actually added it.
_TENANT_INDEX_SQL = """
CREATE INDEX IF NOT EXISTS idx_agent_room_sessions_tenant
    ON agent_room_sessions(tenant_id);
"""


@dataclass(frozen=True, slots=True)
class SessionRecord:
    id: str
    name: str
    created_at: float
    tenant_id: str = "default"


@dataclass(frozen=True, slots=True)
class SessionTaskLink:
    session_id: str
    task_id: str
    attached_at: float


def new_session_id() -> str:
    return f"sess-{uuid.uuid4().hex[:12]}"


class SessionStore:
    """Owns its own aiosqlite connection on the same db_path as the checkpointer.

    Tables are namespaced `agent_room_sessions*` and never collide with
    LangGraph checkpointer tables (`checkpoints / writes / blobs`) or memory
    tables (`agent_room_memory_messages*`).
    """

    def __init__(self) -> None:
        self._conn: aiosqlite.Connection | None = None

    async def initialize(self, db_path: str) -> None:
        self._conn = await aiosqlite.connect(db_path)
        await self._conn.execute("PRAGMA journal_mode=WAL")
        await self._conn.execute("PRAGMA busy_timeout=1000")
        await self._conn.execute("PRAGMA foreign_keys=ON")
        await self._conn.executescript(_SCHEMA_SQL)
        # `CREATE TABLE IF NOT EXISTS` doesn't add columns to an
        # already-existing table from before tenant_id existed (v1.x §6.15).
        # Lightweight migration: try the ALTER, swallow "duplicate column".
        with contextlib.suppress(aiosqlite.OperationalError):
            await self._conn.execute(
                "ALTER TABLE agent_room_sessions ADD COLUMN tenant_id TEXT NOT NULL DEFAULT 'default'"
            )
        # Only safe once the column above is guaranteed to exist.
        await self._conn.executescript(_TENANT_INDEX_SQL)
        await self._conn.commit()

    async def close(self) -> None:
        if self._conn is not None:
            try:
                await self._conn.close()
            finally:
                self._conn = None

    async def create(
        self, name: str, *, session_id: str | None = None, tenant_id: str = "default"
    ) -> SessionRecord:
        if self._conn is None:
            raise RuntimeError("SessionStore not initialized")
        clean = name.strip()
        if not clean:
            raise ValueError("session name must not be empty")
        sid = session_id or new_session_id()
        now = time.time()
        await self._conn.execute(
            "INSERT INTO agent_room_sessions (id, name, created_at, tenant_id) VALUES (?, ?, ?, ?)",
            (sid, clean, now, tenant_id),
        )
        await self._conn.commit()
        return SessionRecord(id=sid, name=clean, created_at=now, tenant_id=tenant_id)

    async def get(self, session_id: str) -> SessionRecord | None:
        if self._conn is None:
            raise RuntimeError("SessionStore not initialized")
        async with self._conn.execute(
            "SELECT id, name, created_at, tenant_id FROM agent_room_sessions WHERE id = ?",
            (session_id,),
        ) as cursor:
            row = await cursor.fetchone()
        if row is None:
            return None
        return SessionRecord(id=row[0], name=row[1], created_at=row[2], tenant_id=row[3])

    async def list_all(self, *, tenant_id: str | None = None) -> list[SessionRecord]:
        """All sessions, or (v1.x §6.15) only those owned by `tenant_id` when given."""
        if self._conn is None:
            raise RuntimeError("SessionStore not initialized")
        if tenant_id is None:
            query = "SELECT id, name, created_at, tenant_id FROM agent_room_sessions ORDER BY created_at DESC"
            params: tuple[str, ...] = ()
        else:
            query = (
                "SELECT id, name, created_at, tenant_id FROM agent_room_sessions "
                "WHERE tenant_id = ? ORDER BY created_at DESC"
            )
            params = (tenant_id,)
        async with self._conn.execute(query, params) as cursor:
            rows = await cursor.fetchall()
        return [SessionRecord(id=r[0], name=r[1], created_at=r[2], tenant_id=r[3]) for r in rows]

    async def delete(self, session_id: str) -> bool:
        if self._conn is None:
            raise RuntimeError("SessionStore not initialized")
        async with self._conn.execute(
            "DELETE FROM agent_room_sessions WHERE id = ?",
            (session_id,),
        ) as cursor:
            removed = cursor.rowcount > 0
        await self._conn.commit()
        return removed

    async def attach_task(self, session_id: str, task_id: str) -> bool:
        """Link a task_id to a session. Returns False if the session is missing."""
        if self._conn is None:
            raise RuntimeError("SessionStore not initialized")
        existing = await self.get(session_id)
        if existing is None:
            return False
        await self._conn.execute(
            "INSERT OR IGNORE INTO agent_room_session_tasks "
            "(session_id, task_id, attached_at) VALUES (?, ?, ?)",
            (session_id, task_id, time.time()),
        )
        await self._conn.commit()
        return True

    async def list_tasks(self, session_id: str) -> list[SessionTaskLink]:
        if self._conn is None:
            raise RuntimeError("SessionStore not initialized")
        async with self._conn.execute(
            "SELECT session_id, task_id, attached_at "
            "FROM agent_room_session_tasks WHERE session_id = ? "
            "ORDER BY attached_at DESC",
            (session_id,),
        ) as cursor:
            rows = await cursor.fetchall()
        return [SessionTaskLink(session_id=r[0], task_id=r[1], attached_at=r[2]) for r in rows]
