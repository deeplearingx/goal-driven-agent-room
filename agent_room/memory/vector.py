"""SQLite-backed vector index with deterministic brute-force cosine search."""

from __future__ import annotations

import json
import math
from typing import Any


class VectorStore:
    def __init__(self, dim: int) -> None:
        self.dim = dim
        self._conn: Any = None

    async def attach(self, conn: Any) -> None:
        self._conn = conn
        await conn.execute(
            "CREATE TABLE IF NOT EXISTS agent_room_memory_vectors "
            "(row_id INTEGER PRIMARY KEY, vector TEXT NOT NULL)"
        )
        await conn.commit()

    async def initialize(self, dim: int) -> None:
        if dim != self.dim:
            raise ValueError(f"vector dimension mismatch: expected {self.dim}, got {dim}")

    async def upsert(self, row_id: int, vector: list[float]) -> None:
        if self._conn is None:
            raise RuntimeError("vector store is not attached")
        if len(vector) != self.dim:
            raise ValueError(f"expected vector dimension {self.dim}, got {len(vector)}")
        await self._conn.execute(
            "INSERT INTO agent_room_memory_vectors(row_id, vector) VALUES (?, ?) "
            "ON CONFLICT(row_id) DO UPDATE SET vector=excluded.vector",
            (row_id, json.dumps(vector)),
        )
        await self._conn.commit()

    async def search(self, vector: list[float], k: int = 5) -> list[tuple[int, float]]:
        if k <= 0 or self._conn is None:
            return []
        cursor = await self._conn.execute("SELECT row_id, vector FROM agent_room_memory_vectors")
        rows = await cursor.fetchall()
        query_norm = math.sqrt(sum(x * x for x in vector)) or 1.0
        scored: list[tuple[int, float]] = []
        for row_id, raw in rows:
            candidate = json.loads(raw)
            norm = math.sqrt(sum(x * x for x in candidate)) or 1.0
            similarity = sum(a * b for a, b in zip(vector, candidate, strict=True)) / (
                query_norm * norm
            )
            scored.append((int(row_id), float(similarity)))
        scored.sort(key=lambda item: item[1], reverse=True)
        return scored[:k]

    async def close(self) -> None:
        return None
