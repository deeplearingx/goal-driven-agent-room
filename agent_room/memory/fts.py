"""SQLite transcript log with FTS5/LIKE search and optional vector recall."""

from __future__ import annotations

import re
import time
import uuid
from typing import Any

import aiosqlite

from agent_room.memory.embedding import EmbeddingBackend
from agent_room.memory.provider import Memory, TranscriptRole
from agent_room.memory.vector import VectorStore
from agent_room.memory.vector_index import VectorIndex

_STOPWORDS = {"the", "and", "of", "to", "a", "an", "in", "is", "it", "for", "on"}


class TranscriptStore:
    def __init__(
        self,
        *,
        embedding: EmbeddingBackend | None = None,
        vector_index: VectorIndex | None = None,
    ) -> None:
        self._embedding = embedding
        self._external_vector = vector_index
        self._vector: VectorStore | VectorIndex | None = None
        self._conn: aiosqlite.Connection | None = None
        self._session_id = ""

    async def initialize(self, db_path: str, session_id: str = "") -> None:
        self._session_id = session_id or f"session-{uuid.uuid4().hex[:12]}"
        self._conn = await aiosqlite.connect(db_path)
        await self._conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS agent_room_memory_messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT NOT NULL,
                role TEXT NOT NULL,
                content TEXT NOT NULL,
                tool_call_id TEXT,
                tool_name TEXT,
                timestamp REAL NOT NULL
            );
            CREATE VIRTUAL TABLE IF NOT EXISTS agent_room_memory_messages_fts
            USING fts5(content, content='agent_room_memory_messages', content_rowid='id');
            CREATE TRIGGER IF NOT EXISTS agent_room_memory_ai AFTER INSERT ON agent_room_memory_messages BEGIN
                INSERT INTO agent_room_memory_messages_fts(rowid, content) VALUES (new.id, new.content);
            END;
            CREATE TRIGGER IF NOT EXISTS agent_room_memory_ad AFTER DELETE ON agent_room_memory_messages BEGIN
                INSERT INTO agent_room_memory_messages_fts(agent_room_memory_messages_fts, rowid, content)
                VALUES ('delete', old.id, old.content);
            END;
            CREATE TRIGGER IF NOT EXISTS agent_room_memory_au AFTER UPDATE ON agent_room_memory_messages BEGIN
                INSERT INTO agent_room_memory_messages_fts(agent_room_memory_messages_fts, rowid, content)
                VALUES ('delete', old.id, old.content);
                INSERT INTO agent_room_memory_messages_fts(rowid, content) VALUES (new.id, new.content);
            END;
            """
        )
        await self._conn.commit()
        if self._embedding is not None:
            if self._external_vector is None:
                local = VectorStore(self._embedding.dim)
                await local.attach(self._conn)
                self._vector = local
            else:
                await self._external_vector.initialize(self._embedding.dim)
                self._vector = self._external_vector

    async def append(
        self,
        role: TranscriptRole,
        content: str,
        *,
        tool_call_id: str | None = None,
        tool_name: str | None = None,
    ) -> None:
        if self._conn is None or not content.strip():
            return
        try:
            cursor = await self._conn.execute(
                "INSERT INTO agent_room_memory_messages"
                "(session_id, role, content, tool_call_id, tool_name, timestamp) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (self._session_id, role, content, tool_call_id, tool_name, time.time()),
            )
            await self._conn.commit()
            if self._embedding is not None and self._vector is not None:
                vector = self._embedding.embed(content)
                row_id = cursor.lastrowid
                if vector is not None and row_id is not None:
                    await self._vector.upsert(int(row_id), vector)
        except Exception:
            # Transcript persistence is explicitly best-effort and must not
            # take down the agent loop.
            return

    async def search(self, query: str, k: int = 5) -> list[tuple[Memory, str]]:
        if self._conn is None or not query.strip() or k <= 0:
            return []
        lexical = await self._lexical_search(query, k)
        if self._embedding is None or self._vector is None:
            return lexical
        vector = self._embedding.embed(query)
        if vector is None:
            return lexical
        hits = await self._vector.search(vector, max(k, 10))
        vector_results: list[tuple[Memory, str]] = []
        for row_id, _score in hits:
            memory = await self._by_id(row_id)
            if memory is not None:
                vector_results.append((memory, _snippet_like(memory.content, query)))
        return _reciprocal_rank_fusion(lexical, vector_results)[:k]

    async def _lexical_search(self, query: str, k: int) -> list[tuple[Memory, str]]:
        assert self._conn is not None
        tokens = [
            token
            for token in re.findall(r"[A-Za-z0-9]+", query)
            if token.lower() not in _STOPWORDS
        ]
        if tokens:
            fts_query = " OR ".join(f'"{token.replace(chr(34), chr(34) * 2)}"' for token in tokens)
            try:
                cursor = await self._conn.execute(
                    "SELECT m.id,m.session_id,m.role,m.content,m.tool_call_id,m.tool_name,m.timestamp,"
                    "snippet(agent_room_memory_messages_fts,0,'>>>','<<<','...',24) "
                    "FROM agent_room_memory_messages_fts f "
                    "JOIN agent_room_memory_messages m ON m.id=f.rowid "
                    "WHERE agent_room_memory_messages_fts MATCH ? ORDER BY rank LIMIT ?",
                    (fts_query, k),
                )
                rows = await cursor.fetchall()
                if rows:
                    return [(_memory(row[:7]), str(row[7])) for row in rows]
            except aiosqlite.Error:
                pass
        needle = query.strip()
        cursor = await self._conn.execute(
            "SELECT id,session_id,role,content,tool_call_id,tool_name,timestamp "
            "FROM agent_room_memory_messages WHERE content LIKE ? ORDER BY id DESC LIMIT ?",
            (f"%{needle}%", k),
        )
        rows = await cursor.fetchall()
        return [(_memory(row), _snippet_like(str(row[3]), needle)) for row in rows]

    async def _by_id(self, row_id: int) -> Memory | None:
        assert self._conn is not None
        cursor = await self._conn.execute(
            "SELECT id,session_id,role,content,tool_call_id,tool_name,timestamp "
            "FROM agent_room_memory_messages WHERE id=?",
            (row_id,),
        )
        row = await cursor.fetchone()
        return _memory(row) if row else None

    async def close(self) -> None:
        if self._vector is not None:
            await self._vector.close()
            self._vector = None
        if self._conn is not None:
            await self._conn.close()
            self._conn = None


def _memory(row: Any) -> Memory:
    return Memory(
        id=int(row[0]),
        session_id=str(row[1]),
        role=row[2],
        content=str(row[3]),
        tool_call_id=row[4],
        tool_name=row[5],
        timestamp=float(row[6]),
    )


def _snippet_like(content: str, query: str) -> str:
    lower = content.lower()
    pos = lower.find(query.lower())
    if pos < 0:
        return content[:160]
    start = max(0, pos - 60)
    end = min(len(content), pos + len(query) + 60)
    return content[start:pos] + ">>>" + content[pos : pos + len(query)] + "<<<" + content[pos + len(query) : end]


def render_snippets(pairs: list[tuple[Memory, str]]) -> str:
    return "\n\n".join(
        f"[{memory.role} @ {memory.session_id[:8]}]\n{snippet}" for memory, snippet in pairs
    )


def _reciprocal_rank_fusion(
    lexical: list[tuple[Memory, str]], vector: list[tuple[Memory, str]]
) -> list[tuple[Memory, str]]:
    scores: dict[int, float] = {}
    values: dict[int, tuple[Memory, str]] = {}
    lexical_ids = {memory.id for memory, _ in lexical}
    for results in (lexical, vector):
        for rank, pair in enumerate(results, 1):
            memory, snippet = pair
            scores[memory.id] = scores.get(memory.id, 0.0) + 1.0 / (60 + rank)
            if memory.id not in values or memory.id not in lexical_ids:
                values[memory.id] = (memory, snippet)
    return [
        values[row_id]
        for row_id in sorted(scores, key=lambda item: scores[item], reverse=True)
    ]
