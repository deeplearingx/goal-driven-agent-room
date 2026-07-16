"""Real Qdrant *server* smoke test (v1.x §6.15) — exercises `url=` mode,
distinct from the offline embedded `path=` mode already covered by
`tests/test_memory_vector_qdrant.py`. Skipped unless `AGENT_ROOM_QDRANT_URL`
points at a reachable server (this repo ships no Qdrant server — embedded
mode is the documented default, see SECURITY.md).

Run explicitly (with a real server up): `pytest tests/integration -q`
"""

from __future__ import annotations

import os

import pytest

pytestmark = pytest.mark.skipif(
    not os.getenv("AGENT_ROOM_QDRANT_URL"),
    reason="set AGENT_ROOM_QDRANT_URL to a reachable Qdrant server to run this test",
)


@pytest.mark.asyncio
async def test_remote_qdrant_roundtrip() -> None:
    from agent_room.memory.qdrant_index import QdrantVectorIndex

    url = os.environ["AGENT_ROOM_QDRANT_URL"]
    index = QdrantVectorIndex(url=url, collection="agent_room_test_live_smoke")
    await index.initialize(4)
    await index.upsert(1, [1.0, 0.0, 0.0, 0.0])
    hits = await index.search([1.0, 0.0, 0.0, 0.0], k=1)
    assert hits and hits[0][0] == 1
    await index.close()
