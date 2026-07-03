"""v0.4 §4.1 — ContextEngine ABC + NoOp default.

Covers:
- ABC enforcement: `ContextEngine()` raises; subclass must implement both.
- NoOp: should_compress always False, compress returns copy.
- apply() convenience: short-circuits when should_compress=False, returns
  fresh list (so caller mutation doesn't bleed back).
- apply() compresses when gate says yes, and the result is itself stable
  under a second apply (idempotence).
- Empty / single-message inputs don't blow up.

Real engines (windowed / summary) ship in §4.2 / §4.3 and get their own
test files.
"""

from __future__ import annotations

from collections.abc import Sequence

import pytest
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage

from agent_room.context import ContextEngine, NoOpContextEngine


def _msgs(*texts: str) -> list[BaseMessage]:
    return [HumanMessage(content=t) for t in texts]


# ---------- ABC enforcement ----------


def test_context_engine_is_abstract():
    """ContextEngine must not be instantiable directly."""
    with pytest.raises(TypeError, match="abstract"):
        ContextEngine()  # type: ignore[abstract]


def test_subclass_missing_methods_is_abstract():
    """Forgetting `compress` (or `should_compress`) keeps the class abstract."""

    class _OnlyGate(ContextEngine):
        def should_compress(self, messages):
            return False

    with pytest.raises(TypeError, match="abstract"):
        _OnlyGate()  # type: ignore[abstract]


# ---------- NoOpContextEngine ----------


def test_noop_should_compress_always_false():
    eng = NoOpContextEngine()
    assert eng.should_compress([]) is False
    assert eng.should_compress(_msgs("a", "b", "c")) is False
    # Even a million-message list — no I/O, no probe — still False.
    big = _msgs(*(f"m{i}" for i in range(1000)))
    assert eng.should_compress(big) is False


@pytest.mark.asyncio
async def test_noop_compress_returns_fresh_list():
    eng = NoOpContextEngine()
    src = _msgs("a", "b")
    out = await eng.compress(src)
    assert out == src
    assert out is not src
    out.append(HumanMessage(content="c"))
    assert len(src) == 2  # caller mutation didn't bleed back


@pytest.mark.asyncio
async def test_noop_apply_passes_through_with_copy():
    """apply() short-circuits should_compress=False but still returns a copy."""
    eng = NoOpContextEngine()
    src = _msgs("a", "b", "c")
    out = await eng.apply(src)
    assert out == src
    assert out is not src


@pytest.mark.asyncio
async def test_noop_apply_handles_empty_and_singleton():
    eng = NoOpContextEngine()
    assert await eng.apply([]) == []
    assert await eng.apply(_msgs("only")) == _msgs("only")


@pytest.mark.asyncio
async def test_noop_apply_preserves_message_types():
    """Mixed System/Human/AI all flow through unchanged."""
    eng = NoOpContextEngine()
    src: list[BaseMessage] = [
        SystemMessage(content="sys"),
        HumanMessage(content="hi"),
        AIMessage(content="hello"),
    ]
    out = await eng.apply(src)
    assert [type(m) for m in out] == [SystemMessage, HumanMessage, AIMessage]
    assert [m.content for m in out] == ["sys", "hi", "hello"]


# ---------- apply() routing ----------


class _GatedEngine(ContextEngine):
    """Test double: compress when len(messages) > limit; lop off the head."""

    def __init__(self, limit: int):
        self.limit = limit
        self.compress_calls = 0

    def should_compress(self, messages: Sequence[BaseMessage]) -> bool:
        return len(messages) > self.limit

    async def compress(self, messages: Sequence[BaseMessage]) -> list[BaseMessage]:
        self.compress_calls += 1
        # Drop everything except the last `limit` messages.
        return list(messages[-self.limit :])


@pytest.mark.asyncio
async def test_apply_routes_to_compress_when_over_budget():
    eng = _GatedEngine(limit=2)
    src = _msgs("a", "b", "c", "d")
    out = await eng.apply(src)
    assert eng.compress_calls == 1
    assert [m.content for m in out] == ["c", "d"]


@pytest.mark.asyncio
async def test_apply_skips_compress_when_under_budget():
    eng = _GatedEngine(limit=10)
    src = _msgs("a", "b", "c")
    out = await eng.apply(src)
    assert eng.compress_calls == 0
    assert [m.content for m in out] == ["a", "b", "c"]
    assert out is not src


@pytest.mark.asyncio
async def test_apply_idempotent_on_second_call():
    """Once compressed below threshold, a second apply must be a no-op.

    Subclass contract item 2: feeding compressed output back must not
    trigger another compress when should_compress is False on it.
    """
    eng = _GatedEngine(limit=2)
    src = _msgs("a", "b", "c", "d")
    once = await eng.apply(src)
    assert eng.compress_calls == 1

    twice = await eng.apply(once)
    # No additional compress — the gate already says it's small enough.
    assert eng.compress_calls == 1
    assert twice == once
    assert twice is not once


@pytest.mark.asyncio
async def test_apply_preserves_order():
    """Subclass contract item 3: compress must not reorder messages."""
    eng = _GatedEngine(limit=3)
    src = _msgs("first", "second", "third", "fourth", "fifth")
    out = await eng.apply(src)
    assert [m.content for m in out] == ["third", "fourth", "fifth"]
