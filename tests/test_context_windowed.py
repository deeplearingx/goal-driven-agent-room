"""v0.4 §4.2 — WindowedContextEngine: head/tail protection + middle truncation.

Covers:
- Construction validation: negative protection windows, max_messages too small.
- should_compress gate at the budget boundary (off-by-one).
- compress shape: head + 1 marker + tail; preserves order; correct count.
- Idempotency: result of compress is itself under-budget (contract item 2).
- Edge cases: empty / under-budget input passes through unchanged; symmetric
  shapes (only-head, only-tail, neither) all valid as long as max_messages
  matches the invariant.
- Marker message kind + payload (HumanMessage with N omitted).
"""

from __future__ import annotations

import pytest
from langchain_core.messages import (
    AIMessage,
    BaseMessage,
    HumanMessage,
    SystemMessage,
    ToolMessage,
)

from agent_room.context import WindowedContextEngine


def _human(*texts: str) -> list[BaseMessage]:
    return [HumanMessage(content=t) for t in texts]


# ---------- construction validation ----------


def test_rejects_negative_protect_first():
    with pytest.raises(ValueError, match="protect_first_n must be >= 0"):
        WindowedContextEngine(max_messages=10, protect_first_n=-1, protect_last_n=3)


def test_rejects_negative_protect_last():
    with pytest.raises(ValueError, match="protect_last_n must be >= 0"):
        WindowedContextEngine(max_messages=10, protect_first_n=2, protect_last_n=-1)


def test_rejects_max_messages_below_window_invariant():
    """max_messages must leave room for protect_first + 1 marker + protect_last."""
    with pytest.raises(ValueError, match="idempotency violation"):
        WindowedContextEngine(max_messages=4, protect_first_n=2, protect_last_n=2)


def test_accepts_minimal_budget():
    """Boundary: max_messages == protect_first + protect_last + 1 is valid."""
    eng = WindowedContextEngine(max_messages=5, protect_first_n=2, protect_last_n=2)
    assert eng.max_messages == 5
    assert eng.protect_first_n == 2
    assert eng.protect_last_n == 2


def test_accepts_zero_protect_first():
    """All-tail protection is a valid configuration (default-ish for ReAct loops)."""
    eng = WindowedContextEngine(max_messages=10, protect_first_n=0, protect_last_n=6)
    assert eng.protect_first_n == 0


def test_accepts_zero_protect_last():
    """Head-only protection is also valid (rare but legal — e.g. system-prompt-only)."""
    eng = WindowedContextEngine(max_messages=10, protect_first_n=3, protect_last_n=0)
    assert eng.protect_last_n == 0


# ---------- should_compress gate ----------


def test_should_compress_strict_inequality():
    eng = WindowedContextEngine(max_messages=5, protect_first_n=1, protect_last_n=2)
    assert eng.should_compress(_human("a", "b", "c", "d", "e")) is False  # exactly 5
    assert eng.should_compress(_human("a", "b", "c", "d", "e", "f")) is True  # 6


def test_should_compress_empty():
    eng = WindowedContextEngine(max_messages=5, protect_first_n=1, protect_last_n=2)
    assert eng.should_compress([]) is False


# ---------- compress shape ----------


@pytest.mark.asyncio
async def test_compress_under_budget_passes_through():
    eng = WindowedContextEngine(max_messages=5, protect_first_n=1, protect_last_n=2)
    src = _human("a", "b", "c")
    out = await eng.compress(src)
    assert [m.content for m in out] == ["a", "b", "c"]
    assert out is not src  # always a fresh list


@pytest.mark.asyncio
async def test_compress_keeps_head_and_tail_drops_middle():
    eng = WindowedContextEngine(max_messages=5, protect_first_n=2, protect_last_n=2)
    src = _human("h0", "h1", "m1", "m2", "m3", "m4", "t1", "t2")
    out = await eng.compress(src)
    # 2 head + 1 marker + 2 tail = 5
    assert len(out) == 5
    assert [m.content for m in out[:2]] == ["h0", "h1"]
    assert [m.content for m in out[-2:]] == ["t1", "t2"]
    # 8 - 2 - 2 = 4 middle messages omitted
    assert isinstance(out[2], HumanMessage)
    assert "4 messages omitted" in out[2].content


@pytest.mark.asyncio
async def test_compress_only_tail_protection():
    eng = WindowedContextEngine(max_messages=4, protect_first_n=0, protect_last_n=3)
    src = _human("m1", "m2", "m3", "m4", "m5", "m6")
    out = await eng.compress(src)
    # 0 head + 1 marker + 3 tail = 4
    assert len(out) == 4
    assert [m.content for m in out[1:]] == ["m4", "m5", "m6"]
    assert "3 messages omitted" in out[0].content


@pytest.mark.asyncio
async def test_compress_only_head_protection():
    eng = WindowedContextEngine(max_messages=4, protect_first_n=3, protect_last_n=0)
    src = _human("h1", "h2", "h3", "m1", "m2", "m3")
    out = await eng.compress(src)
    # 3 head + 1 marker + 0 tail = 4
    assert len(out) == 4
    assert [m.content for m in out[:3]] == ["h1", "h2", "h3"]
    assert "3 messages omitted" in out[3].content


@pytest.mark.asyncio
async def test_compress_preserves_message_types():
    """System / Human / AI in head and tail bands all survive untouched."""
    eng = WindowedContextEngine(max_messages=4, protect_first_n=1, protect_last_n=2)
    src: list[BaseMessage] = [
        SystemMessage(content="sys"),
        HumanMessage(content="hu1"),
        AIMessage(content="ai1"),
        HumanMessage(content="hu2"),
        AIMessage(content="ai2"),
        HumanMessage(content="hu3"),
        AIMessage(content="ai3"),
    ]
    out = await eng.compress(src)
    assert isinstance(out[0], SystemMessage)
    assert isinstance(out[1], HumanMessage)  # marker
    assert isinstance(out[2], HumanMessage)  # last 2
    assert isinstance(out[3], AIMessage)


# ---------- apply() routing + idempotency ----------


@pytest.mark.asyncio
async def test_apply_compresses_then_idempotent():
    """After one apply the result is below budget; a second apply is a no-op."""
    eng = WindowedContextEngine(max_messages=5, protect_first_n=1, protect_last_n=2)
    src = _human(*[f"m{i}" for i in range(20)])
    once = await eng.apply(src)
    assert len(once) == 4  # 1 + 1 + 2
    # Below budget now (4 <= 5), so second apply only copies, doesn't compress.
    twice = await eng.apply(once)
    assert twice == once
    assert twice is not once
    # Marker count must not have doubled.
    markers = [m for m in twice if "messages omitted" in m.content]
    assert len(markers) == 1


@pytest.mark.asyncio
async def test_compressed_result_is_under_budget():
    """Invariant the constructor enforces — verify it actually holds at runtime."""
    eng = WindowedContextEngine(max_messages=10, protect_first_n=3, protect_last_n=6)
    src = _human(*[f"m{i}" for i in range(50)])
    out = await eng.compress(src)
    assert len(out) <= eng.max_messages
    assert eng.should_compress(out) is False


# ---------- preserves conversational order ----------


@pytest.mark.asyncio
async def test_compress_preserves_relative_order():
    """Head bands and tail bands keep their internal ordering; tail comes after head."""
    eng = WindowedContextEngine(max_messages=6, protect_first_n=2, protect_last_n=3)
    src = _human("first", "second", "x", "x", "x", "x", "third-last", "second-last", "last")
    out = await eng.compress(src)
    contents = [m.content for m in out]
    assert contents[0] == "first"
    assert contents[1] == "second"
    # contents[2] is the marker
    assert contents[3] == "third-last"
    assert contents[4] == "second-last"
    assert contents[5] == "last"


# ---------- ReAct boundary alignment ----------


def _ai_with_tool_call(text: str, *, tool_name: str = "read_text") -> AIMessage:
    """AIMessage carrying a tool_call (the kind ToolNode fans out on)."""
    return AIMessage(
        content=text,
        tool_calls=[{"name": tool_name, "args": {}, "id": f"call-{text}"}],
    )


def _tool_msg(text: str, *, call_id: str) -> ToolMessage:
    return ToolMessage(content=text, tool_call_id=call_id)


@pytest.mark.asyncio
async def test_compress_drops_orphan_tool_message_at_tail_start():
    """Tail starting with ToolMessage whose AI parent was dropped → drop the orphan.

    Anthropic / many providers reject `ToolMessage` not preceded by an
    `AIMessage(tool_calls=[...])` with the matching id. Instead of producing
    a request the provider will refuse, the engine prefers a slightly
    shorter tail.
    """
    eng = WindowedContextEngine(max_messages=4, protect_first_n=1, protect_last_n=2)
    src: list[BaseMessage] = [
        SystemMessage(content="sys"),  # head
        HumanMessage(content="task"),
        _ai_with_tool_call("read"),  # would-be tail boundary partner
        _tool_msg("file contents", call_id="call-read"),  # tail[0] — orphan after slice
        AIMessage(content="final"),  # tail[1]
    ]
    out = await eng.compress(src)
    # 1 head + 1 marker + 1 tail (orphan ToolMessage was dropped)
    assert len(out) == 3
    assert isinstance(out[0], SystemMessage)
    assert "messages omitted" in out[1].content
    assert isinstance(out[2], AIMessage)
    assert out[2].content == "final"


@pytest.mark.asyncio
async def test_compress_drops_dangling_ai_tool_call_at_head_end():
    """Head ending with AIMessage(tool_calls=...) → drop it; matching Tool was elided.

    Reverse of the orphan-tool case: if the AI's tool_calls request landed
    in head but the ToolMessage answer was in the dropped middle, the
    provider rejects an unanswered tool_call. Drop the dangling AI.
    """
    eng = WindowedContextEngine(max_messages=4, protect_first_n=2, protect_last_n=1)
    src: list[BaseMessage] = [
        SystemMessage(content="sys"),  # head[0]
        _ai_with_tool_call("read1"),  # head[1] — dangling after slice
        _tool_msg("contents1", call_id="call-read1"),  # middle (dropped)
        _ai_with_tool_call("read2"),  # middle (dropped)
        _tool_msg("contents2", call_id="call-read2"),  # middle (dropped)
        AIMessage(content="final"),  # tail
    ]
    out = await eng.compress(src)
    # 1 head (dangling AI dropped) + 1 marker + 1 tail
    assert len(out) == 3
    assert isinstance(out[0], SystemMessage)
    assert "messages omitted" in out[1].content
    assert isinstance(out[2], AIMessage)
    assert out[2].content == "final"


@pytest.mark.asyncio
async def test_compress_drops_consecutive_orphan_tool_messages():
    """Multiple parallel tool_calls produce multiple ToolMessages — drop all orphans."""
    eng = WindowedContextEngine(max_messages=5, protect_first_n=1, protect_last_n=3)
    src: list[BaseMessage] = [
        SystemMessage(content="sys"),
        HumanMessage(content="task"),
        _ai_with_tool_call("a"),  # answered by next two
        _tool_msg("a1", call_id="call-a"),
        _tool_msg("a2", call_id="call-a"),  # tail starts here
        AIMessage(content="done"),
    ]
    # tail = last 3 = [tool_msg, tool_msg, AI("done")]; both ToolMessages orphan
    out = await eng.compress(src)
    assert len(out) == 3  # 1 head + 1 marker + 1 tail (only AI("done") survives)
    assert isinstance(out[2], AIMessage)
    assert out[2].content == "done"
