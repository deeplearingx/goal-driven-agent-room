"""v1.x §6.9-4 — hybrid semantic/vector recall (sqlite-vec + hashing embedding).

Pins: `HashingEmbeddingBackend` determinism, `VectorStore` KNN round-trip,
`TranscriptStore` hybrid fusion (RRF), NoOp-default zero-behavior-change, and
an ablation comparing FTS5-only vs. hybrid hit-rate on a CJK paraphrase set —
the number PLAN.md §6.9-4 asks for.
"""

from __future__ import annotations

import math
from pathlib import Path

import pytest

from agent_room.memory.embedding import HashingEmbeddingBackend, NoOpEmbeddingBackend
from agent_room.memory.file_fts import FileFtsMemoryProvider
from agent_room.memory.fts import TranscriptStore, _reciprocal_rank_fusion
from agent_room.memory.provider import Memory
from agent_room.memory.vector import VectorStore

# -------------------- HashingEmbeddingBackend --------------------


def test_hashing_backend_is_deterministic() -> None:
    backend = HashingEmbeddingBackend()
    v1 = backend.embed("the quick brown fox")
    v2 = backend.embed("the quick brown fox")
    assert v1 == v2


def test_hashing_backend_empty_text_returns_none() -> None:
    backend = HashingEmbeddingBackend()
    assert backend.embed("") is None
    assert backend.embed("   \n\t  ") is None


def test_hashing_backend_vector_is_l2_normalized() -> None:
    backend = HashingEmbeddingBackend()
    vec = backend.embed("normalize me please")
    assert vec is not None
    norm = math.sqrt(sum(x * x for x in vec))
    assert abs(norm - 1.0) < 1e-6


def test_hashing_backend_different_text_different_vector() -> None:
    backend = HashingEmbeddingBackend()
    v1 = backend.embed("apples and oranges")
    v2 = backend.embed("completely unrelated topic about spacecraft")
    assert v1 != v2


def test_hashing_backend_respects_dim() -> None:
    backend = HashingEmbeddingBackend(dim=64)
    vec = backend.embed("some text")
    assert vec is not None
    assert len(vec) == 64


def test_noop_embedding_backend_always_none() -> None:
    backend = NoOpEmbeddingBackend()
    assert backend.dim == 0
    assert backend.embed("anything") is None


# -------------------- VectorStore --------------------


@pytest.mark.asyncio
async def test_vector_store_knn_returns_nearest_first(tmp_path: Path) -> None:
    import aiosqlite

    conn = await aiosqlite.connect(str(tmp_path / "vec.db"))
    store = VectorStore(dim=4)
    await store.attach(conn)
    await store.upsert(1, [1.0, 0.0, 0.0, 0.0])
    await store.upsert(2, [0.0, 1.0, 0.0, 0.0])
    await store.upsert(3, [0.9, 0.1, 0.0, 0.0])
    hits = await store.search([1.0, 0.0, 0.0, 0.0], k=2)
    assert [h[0] for h in hits] == [1, 3]
    await conn.close()


@pytest.mark.asyncio
async def test_vector_store_k_zero_returns_empty(tmp_path: Path) -> None:
    import aiosqlite

    conn = await aiosqlite.connect(str(tmp_path / "vec.db"))
    store = VectorStore(dim=4)
    await store.attach(conn)
    await store.upsert(1, [1.0, 0.0, 0.0, 0.0])
    assert await store.search([1.0, 0.0, 0.0, 0.0], k=0) == []
    await conn.close()


# -------------------- reciprocal rank fusion --------------------


def _mem(id_: int) -> Memory:
    return Memory(
        id=id_,
        session_id="s",
        role="assistant",
        content=f"content-{id_}",
        tool_call_id=None,
        tool_name=None,
        timestamp=0.0,
    )


def test_rrf_prefers_item_ranked_in_both_lists() -> None:
    lexical = [(_mem(1), "a"), (_mem(2), "b")]
    vector = [(_mem(2), "b"), (_mem(3), "c")]
    fused = _reciprocal_rank_fusion(lexical, vector)
    ids = [m.id for m, _ in fused]
    assert ids[0] == 2  # appears top-ish in both lists
    assert set(ids) == {1, 2, 3}


def test_rrf_keeps_lexical_snippet_on_overlap() -> None:
    lexical = [(_mem(1), "lexical-snippet")]
    vector = [(_mem(1), "vector-snippet")]
    fused = _reciprocal_rank_fusion(lexical, vector)
    assert fused[0][1] == "lexical-snippet"


def test_rrf_empty_inputs() -> None:
    assert _reciprocal_rank_fusion([], []) == []


# -------------------- TranscriptStore hybrid wiring --------------------


@pytest.fixture
async def hybrid_store(tmp_path: Path):
    s = TranscriptStore(embedding=HashingEmbeddingBackend())
    await s.initialize(str(tmp_path / "agent_room.db"), session_id="sess-hybrid")
    yield s
    await s.close()


@pytest.mark.asyncio
async def test_hybrid_store_still_finds_exact_lexical_match(hybrid_store: TranscriptStore) -> None:
    await hybrid_store.append("assistant", "implemented OAuth2 token refresh logic")
    pairs = await hybrid_store.search("OAuth2", k=5)
    assert any("OAuth2" in m.content for m, _ in pairs)


@pytest.mark.asyncio
async def test_hybrid_store_appends_without_embedding_backend_unchanged(tmp_path: Path) -> None:
    """embedding=None (the default) must behave exactly like v0.5 — no vector
    table, no fusion, pure lexical search."""
    s = TranscriptStore()
    await s.initialize(str(tmp_path / "plain.db"))
    await s.append("assistant", "plain fact about widgets")
    pairs = await s.search("widgets", k=5)
    assert len(pairs) == 1
    await s.close()


@pytest.mark.asyncio
async def test_hybrid_store_vector_layer_populated_on_append(hybrid_store: TranscriptStore) -> None:
    await hybrid_store.append("assistant", "a fact worth embedding")
    assert hybrid_store._vector is not None
    embedding = HashingEmbeddingBackend().embed("a fact worth embedding")
    assert embedding is not None
    hits = await hybrid_store._vector.search(embedding, k=1)
    assert len(hits) == 1


@pytest.mark.asyncio
async def test_file_fts_provider_wires_embedding_through(tmp_path: Path) -> None:
    provider = FileFtsMemoryProvider(embedding=HashingEmbeddingBackend())
    await provider.initialize(root_dir=tmp_path, db_path=str(tmp_path / "agent_room.db"))
    await provider.sync_turn("assistant", "hybrid recall wiring check")
    block = await provider.prefetch("hybrid recall wiring check", k=3)
    assert "wiring" in block and "check" in block
    await provider.close()


# -------------------- ablation: FTS5-only vs. hybrid hit-rate --------------

# CJK paraphrases: same meaning, no shared multi-char substring with the
# stored fact, so FTS5 MATCH (word tokens) and the LIKE substring fallback
# both miss. Character-trigram hashing still shares n-grams with the stored
# text (e.g. "库", "用异步", "SQLite" fragments), so the hybrid layer picks
# some of these up. This is the honest scope: hashing embeddings are a
# character-similarity signal, not deep semantics — it doesn't close the gap
# on real synonym paraphrases with zero character overlap (documented in
# PLAN.md §6.14 as a known limit of the bundled zero-dependency backend).
_ABLATION_FACTS = [
    "预算熔断器在超过数量上限的时候会立刻抛出异常并终止当前任务",
    "护栏层会在工具返回的结果重新进入大模型上下文之前先扫描一遍内容",
    "开发者节点在调用写文件工具之前会先经过守卫的内容检查",
    "数据库连接使用预写日志模式来提升并发写入的性能表现",
]
_ABLATION_QUERIES = [
    "当前任务被终止是因为数量超过了预算熔断器设置的上限所以抛出了异常",
    "大模型上下文之前工具返回结果会被护栏层先扫描一遍内容",
    "写文件工具调用前守卫会先检查一遍要写入的内容",
    "并发写入性能表现的提升来自于预写日志模式的数据库连接方式",
]


@pytest.mark.asyncio
async def test_ablation_hybrid_hit_rate_meets_or_beats_fts_only(tmp_path: Path) -> None:
    fts_only = TranscriptStore()
    await fts_only.initialize(str(tmp_path / "fts_only.db"), session_id="ablation")
    hybrid = TranscriptStore(embedding=HashingEmbeddingBackend())
    await hybrid.initialize(str(tmp_path / "hybrid.db"), session_id="ablation")

    for fact in _ABLATION_FACTS:
        await fts_only.append("assistant", fact)
        await hybrid.append("assistant", fact)

    def hits(store_results: list[tuple[Memory, str]], expected_fact: str) -> bool:
        return any(expected_fact == m.content for m, _ in store_results)

    fts_hit_count = 0
    hybrid_hit_count = 0
    for query, expected_fact in zip(_ABLATION_QUERIES, _ABLATION_FACTS, strict=True):
        fts_hit_count += hits(await fts_only.search(query, k=3), expected_fact)
        hybrid_hit_count += hits(await hybrid.search(query, k=3), expected_fact)

    await fts_only.close()
    await hybrid.close()

    total = len(_ABLATION_QUERIES)
    print(
        f"\n[ablation] FTS5-only hit-rate: {fts_hit_count}/{total}  "
        f"hybrid hit-rate: {hybrid_hit_count}/{total}"
    )
    assert hybrid_hit_count >= fts_hit_count
    assert hybrid_hit_count > fts_hit_count, (
        "hybrid recall should strictly beat FTS5-only on at least one CJK "
        "paraphrase in this fixture set — otherwise the vector layer isn't "
        "adding recall value here"
    )
