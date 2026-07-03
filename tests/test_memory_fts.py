"""v0.5 §5.3 — transcript SQLite + FTS5 backend.

Pins: schema creation, FTS5 trigger correctness (insert/delete/update),
snippet highlighting, CJK fallback, session isolation, async append non-
blocking, table namespace disjoint from LangGraph checkpointer.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from agent_room.memory.fts import TranscriptStore, render_snippets


@pytest.fixture
async def store(tmp_path: Path) -> TranscriptStore:
    s = TranscriptStore()
    db_path = str(tmp_path / "agent_room.db")
    await s.initialize(db_path, session_id="sess-test")
    yield s
    await s.close()


# -------------------- schema --------------------


@pytest.mark.asyncio
async def test_initialize_creates_disjoint_tables(tmp_path: Path) -> None:
    """Schema uses agent_room_memory_* prefix to coexist with checkpointer."""
    import aiosqlite

    db_path = str(tmp_path / "shared.db")
    s = TranscriptStore()
    await s.initialize(db_path)
    # Simulate checkpointer-style table on the same DB.
    async with aiosqlite.connect(db_path) as conn:
        await conn.execute("CREATE TABLE checkpoints (id INTEGER PRIMARY KEY)")
        await conn.commit()
        cur = await conn.execute(
            "SELECT name FROM sqlite_master WHERE type IN ('table','index') ORDER BY name"
        )
        names = {row[0] for row in await cur.fetchall()}
    await s.close()
    assert "agent_room_memory_messages" in names
    assert "agent_room_memory_messages_fts" in names
    assert "checkpoints" in names  # didn't collide


# -------------------- append + search round-trip --------------------


@pytest.mark.asyncio
async def test_append_then_search_returns_match(store: TranscriptStore) -> None:
    await store.append("assistant", "I refactored the auth middleware today")
    await store.append("user", "remember to update the changelog")
    pairs = await store.search("auth middleware", k=5)
    assert len(pairs) >= 1
    mem, snip = pairs[0]
    assert "auth middleware" in mem.content
    assert ">>>" in snip and "<<<" in snip  # FTS5 highlights


@pytest.mark.asyncio
async def test_search_empty_query_returns_empty(store: TranscriptStore) -> None:
    await store.append("assistant", "anything")
    assert await store.search("") == []
    assert await store.search("   ") == []


@pytest.mark.asyncio
async def test_append_skips_blank_content(store: TranscriptStore) -> None:
    await store.append("assistant", "")
    await store.append("assistant", "   \n\t  ")
    pairs = await store.search("anything", k=5)
    assert pairs == []


# -------------------- FTS5 trigger correctness --------------------


@pytest.mark.asyncio
async def test_delete_removes_from_fts_index(store: TranscriptStore, tmp_path: Path) -> None:
    """The 'delete' pseudo-command pattern must keep FTS in sync on DELETE."""
    import aiosqlite

    await store.append("assistant", "ephemeral fact about quantum tunneling")
    pairs = await store.search("quantum", k=5)
    assert len(pairs) == 1
    target_id = pairs[0][0].id

    # Delete via raw SQL (simulates a future cleanup tool).
    db_path = str(tmp_path / "agent_room.db")
    async with aiosqlite.connect(db_path) as conn:
        await conn.execute("DELETE FROM agent_room_memory_messages WHERE id = ?", (target_id,))
        await conn.commit()

    # Now the FTS index must NOT return the deleted row.
    pairs2 = await store.search("quantum", k=5)
    assert pairs2 == [], "deleted row still findable via FTS — trigger broken"


@pytest.mark.asyncio
async def test_update_resyncs_fts_index(store: TranscriptStore, tmp_path: Path) -> None:
    import aiosqlite

    # Use truly disjoint markers: FTS5's default tokenizer treats `_` as a
    # token separator, so `marker_alpha` and `marker_beta` would share the
    # `marker` token under our OR rewrite. Using fully distinct words pins
    # the trigger behavior we actually care about (UPDATE → FTS resync).
    await store.append("assistant", "old content with zalpha")
    pairs = await store.search("zalpha", k=5)
    assert len(pairs) == 1
    target_id = pairs[0][0].id

    db_path = str(tmp_path / "agent_room.db")
    async with aiosqlite.connect(db_path) as conn:
        await conn.execute(
            "UPDATE agent_room_memory_messages SET content = ? WHERE id = ?",
            ("new content with zbeta", target_id),
        )
        await conn.commit()

    # Old token gone, new token findable.
    assert await store.search("zalpha", k=5) == []
    pairs2 = await store.search("zbeta", k=5)
    assert len(pairs2) == 1
    assert pairs2[0][0].id == target_id


# -------------------- CJK fallback --------------------


@pytest.mark.asyncio
async def test_cjk_query_falls_back_to_like(store: TranscriptStore) -> None:
    """FTS5 default tokenizer can't match Chinese phrases — LIKE picks up the slack."""
    await store.append("user", "用户偏好 Python 3.11 以上版本")
    await store.append("assistant", "记录了用户偏好")
    pairs = await store.search("用户偏好", k=5)
    assert len(pairs) >= 1
    found_contents = [m.content for m, _ in pairs]
    assert any("用户偏好" in c for c in found_contents)


@pytest.mark.asyncio
async def test_cjk_snippet_window_around_match(store: TranscriptStore) -> None:
    long_text = "前面" * 50 + "关键词 出现在这里 后面" + "尾巴" * 50
    await store.append("assistant", long_text)
    pairs = await store.search("关键词", k=5)
    assert len(pairs) == 1
    _, snip = pairs[0]
    # Snippet should be a 120-char window, much smaller than long_text (~250 chars).
    assert len(snip) <= 200
    assert "关键词" in snip


@pytest.mark.asyncio
async def test_mixed_latin_query_uses_fts(store: TranscriptStore) -> None:
    """Latin queries should use FTS path (faster, ranked, with highlights)."""
    await store.append("assistant", "implemented OAuth2 token refresh logic")
    pairs = await store.search("OAuth2", k=5)
    assert len(pairs) >= 1
    _, snip = pairs[0]
    assert ">>>OAuth2<<<" in snip or ">>>" in snip  # FTS highlight present


# -------------------- session isolation --------------------


@pytest.mark.asyncio
async def test_session_id_recorded_on_each_row(tmp_path: Path) -> None:
    s1 = TranscriptStore()
    s2 = TranscriptStore()
    db_path = str(tmp_path / "shared.db")
    await s1.initialize(db_path, session_id="sess-1")
    await s2.initialize(db_path, session_id="sess-2")
    await s1.append("assistant", "fact from session 1")
    await s2.append("assistant", "fact from session 2")
    # Search via either store sees both — no row-level filtering by session.
    # That's intentional: cross-session recall is the whole point.
    pairs = await s1.search("fact", k=5)
    sessions_seen = {m.session_id for m, _ in pairs}
    assert sessions_seen == {"sess-1", "sess-2"}
    await s1.close()
    await s2.close()


@pytest.mark.asyncio
async def test_default_session_id_when_unset(tmp_path: Path) -> None:
    s = TranscriptStore()
    await s.initialize(str(tmp_path / "x.db"))
    await s.append("assistant", "auto-session message")
    pairs = await s.search("auto-session", k=5)
    assert len(pairs) == 1
    assert pairs[0][0].session_id.startswith("session-")
    await s.close()


# -------------------- non-blocking append --------------------


@pytest.mark.asyncio
async def test_append_failure_does_not_raise(tmp_path: Path) -> None:
    """If the connection is None or DB is closed, append must swallow."""
    s = TranscriptStore()
    # Never initialized — _conn is None.
    await s.append("assistant", "swallowed silently")  # must not raise


@pytest.mark.asyncio
async def test_close_idempotent(tmp_path: Path) -> None:
    s = TranscriptStore()
    await s.initialize(str(tmp_path / "x.db"))
    await s.close()
    await s.close()  # second close must not raise


# -------------------- render_snippets --------------------


@pytest.mark.asyncio
async def test_render_snippets_formats_with_role_and_session_prefix(
    store: TranscriptStore,
) -> None:
    await store.append("assistant", "some retrievable fact about widgets")
    pairs = await store.search("widgets", k=5)
    rendered = render_snippets(pairs)
    assert "[assistant @ sess-tes]" in rendered
    assert "widgets" in rendered


def test_render_snippets_empty_returns_empty() -> None:
    assert render_snippets([]) == ""


# -------------------- malformed FTS query tolerance --------------------


@pytest.mark.asyncio
async def test_special_chars_in_query_dont_crash(store: TranscriptStore) -> None:
    await store.append("assistant", "edge case content")
    # FTS5 treats `"` and `(` as special; should fall through to LIKE.
    pairs = await store.search('edge "case (content', k=5)
    # Don't care about result count — just that it doesn't raise.
    assert isinstance(pairs, list)


# -------------------- multi-word OR rewrite --------------------


@pytest.mark.asyncio
async def test_multi_word_query_uses_or_semantics(store: TranscriptStore) -> None:
    """A natural-language phrase should hit any matching word, not require
    every word — that's the load-bearing recall fix from §5.6 demo."""
    await store.append("assistant", "implementing OAuth2 token refresh in auth/refresh.py")
    # Query has words ('now', 'refactor', 'handling') the stored content
    # lacks. Default FTS5 AND semantics would return zero hits; OR rewrite
    # finds it via 'OAuth2' / 'token'.
    pairs = await store.search("now refactor the OAuth2 token handling", k=5)
    assert len(pairs) == 1
    assert "OAuth2" in pairs[0][0].content


@pytest.mark.asyncio
async def test_stopword_only_query_returns_empty(store: TranscriptStore) -> None:
    """A query of nothing but stopwords should not match everything — we
    fall through to LIKE which substring-matches the original phrase, and
    the original is too long/noisy to coincide with a stored row."""
    await store.append("assistant", "real content")
    pairs = await store.search("the and of to", k=5)
    # All four are stopwords → FTS query rewrite yields '' → FTS path
    # skipped entirely → LIKE path with 'the and of to' substring → no hit.
    assert pairs == []
