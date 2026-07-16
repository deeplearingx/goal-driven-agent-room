"""v1.x §6.15 — pluggable vector backend: `QdrantVectorIndex` + wiring into
`TranscriptStore`/`FileFtsMemoryProvider`.

All offline: `QdrantClient(path=...)` is a local embedded index (no network,
no server process) — same zero-network guarantee as the sqlite-vec tests.
Real model download (`fastembed`) tests live in `tests/integration/`.
`pytest.importorskip` lets this file degrade to "skipped" (not "error") in
an environment without the `[vector]` extra installed.
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("qdrant_client")

from agent_room.memory.embedding import HashingEmbeddingBackend  # noqa: E402
from agent_room.memory.file_fts import FileFtsMemoryProvider  # noqa: E402
from agent_room.memory.fts import TranscriptStore  # noqa: E402
from agent_room.memory.qdrant_index import QdrantVectorIndex  # noqa: E402

# -------------------- QdrantVectorIndex --------------------


@pytest.mark.asyncio
async def test_initialize_creates_collection_with_correct_dim(tmp_path: Path) -> None:
    index = QdrantVectorIndex(path=str(tmp_path / "qdrant"))
    await index.initialize(4)
    hits = await index.search([1.0, 0.0, 0.0, 0.0], k=5)
    assert hits == []
    await index.close()


@pytest.mark.asyncio
async def test_upsert_then_search_returns_nearest_first(tmp_path: Path) -> None:
    index = QdrantVectorIndex(path=str(tmp_path / "qdrant"))
    await index.initialize(4)
    await index.upsert(1, [1.0, 0.0, 0.0, 0.0])
    await index.upsert(2, [0.0, 1.0, 0.0, 0.0])
    await index.upsert(3, [0.9, 0.1, 0.0, 0.0])
    hits = await index.search([1.0, 0.0, 0.0, 0.0], k=2)
    assert [h[0] for h in hits] == [1, 3]
    await index.close()


@pytest.mark.asyncio
async def test_search_k_zero_returns_empty(tmp_path: Path) -> None:
    index = QdrantVectorIndex(path=str(tmp_path / "qdrant"))
    await index.initialize(4)
    await index.upsert(1, [1.0, 0.0, 0.0, 0.0])
    assert await index.search([1.0, 0.0, 0.0, 0.0], k=0) == []
    await index.close()


@pytest.mark.asyncio
async def test_reinitialize_same_dim_is_noop(tmp_path: Path) -> None:
    path = str(tmp_path / "qdrant")
    index = QdrantVectorIndex(path=path)
    await index.initialize(4)
    await index.upsert(1, [1.0, 0.0, 0.0, 0.0])
    await index.close()

    index2 = QdrantVectorIndex(path=path)
    await index2.initialize(4)  # same dim — must not raise, must not wipe data
    hits = await index2.search([1.0, 0.0, 0.0, 0.0], k=5)
    assert len(hits) == 1
    await index2.close()


@pytest.mark.asyncio
async def test_reinitialize_different_dim_raises(tmp_path: Path) -> None:
    path = str(tmp_path / "qdrant")
    index = QdrantVectorIndex(path=path)
    await index.initialize(4)
    await index.close()

    index2 = QdrantVectorIndex(path=path)
    with pytest.raises(ValueError, match="vector size"):
        await index2.initialize(8)
    await index2.close()


@pytest.mark.asyncio
async def test_close_releases_lock_for_reopen(tmp_path: Path) -> None:
    path = str(tmp_path / "qdrant")
    index = QdrantVectorIndex(path=path)
    await index.initialize(4)
    await index.close()

    # If close() didn't release the file lock, this would hang/raise.
    index2 = QdrantVectorIndex(path=path)
    await index2.initialize(4)
    await index2.close()


def test_requires_path_or_url() -> None:
    with pytest.raises(ValueError, match="path=.*url="):
        QdrantVectorIndex()


# -------------------- TranscriptStore wiring --------------------


@pytest.mark.asyncio
async def test_transcript_store_uses_supplied_vector_index(tmp_path: Path) -> None:
    """embedding + vector_index both given → TranscriptStore must call
    `.initialize(dim)` on the supplied index, not build its own VectorStore."""
    qdrant = QdrantVectorIndex(path=str(tmp_path / "qdrant"))
    store = TranscriptStore(embedding=HashingEmbeddingBackend(), vector_index=qdrant)
    await store.initialize(str(tmp_path / "agent_room.db"), session_id="sess-qdrant")
    assert store._vector is qdrant

    await store.append("assistant", "a fact indexed via qdrant instead of sqlite-vec")
    pairs = await store.search("indexed via qdrant", k=5)
    assert any("qdrant" in m.content for m, _ in pairs)
    await store.close()


@pytest.mark.asyncio
async def test_file_fts_provider_wires_vector_index_through(tmp_path: Path) -> None:
    qdrant = QdrantVectorIndex(path=str(tmp_path / "qdrant"))
    provider = FileFtsMemoryProvider(embedding=HashingEmbeddingBackend(), vector_index=qdrant)
    await provider.initialize(root_dir=tmp_path, db_path=str(tmp_path / "agent_room.db"))
    await provider.sync_turn("assistant", "qdrant wiring end to end check")
    block = await provider.prefetch("qdrant wiring end to end check", k=3)
    assert "wiring" in block
    await provider.close()
