"""v0.4 §3.4 — spill store + per-message helper unit tests.

Pins the small public surface of `agent_room.tools.spill`:

- `InMemorySpillStore.put` is content-addressable: same payload → same ref,
  no duplicate write, idempotent across calls.
- `maybe_spill_tool_message` skips messages under threshold; replaces big
  ones with a `[spill: ref=...]` placeholder; preserves `tool_call_id`.
- The placeholder shape is itself idempotent: running spill twice on an
  already-spilled message returns the same body (no double-wrap).
- `spill_messages` walks a list, only touching `ToolMessage`s.
"""

from __future__ import annotations

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from agent_room.tools.spill import (
    DEFAULT_SPILL_THRESHOLD,
    InMemorySpillStore,
    SpillRef,
    maybe_spill_tool_message,
    spill_messages,
)

# ---------- store ----------


def test_store_roundtrip():
    store = InMemorySpillStore()
    payload = "x" * 10_000
    ref = store.put(payload)
    assert isinstance(ref, SpillRef)
    assert ref.size == 10_000
    assert store.get(ref.ref) == payload


def test_store_is_content_addressable_and_idempotent():
    """Same payload → same ref → single physical entry."""
    store = InMemorySpillStore()
    payload = "abc" * 5_000
    r1 = store.put(payload)
    r2 = store.put(payload)
    assert r1.ref == r2.ref
    assert len(store) == 1


def test_store_distinguishes_different_payloads():
    store = InMemorySpillStore()
    r_a = store.put("payload-A")
    r_b = store.put("payload-B")
    assert r_a.ref != r_b.ref
    assert len(store) == 2
    assert store.get(r_a.ref) == "payload-A"
    assert store.get(r_b.ref) == "payload-B"


def test_store_get_unknown_returns_none():
    assert InMemorySpillStore().get("deadbeefdead") is None


# ---------- maybe_spill_tool_message ----------


def test_under_threshold_passes_through_unchanged():
    store = InMemorySpillStore()
    msg = ToolMessage(content="small output", tool_call_id="c1")
    out = maybe_spill_tool_message(msg, store, threshold=1000)
    # Same identity is allowed but not required; the contract is
    # that content + tool_call_id are unchanged and nothing was stored.
    assert out.content == "small output"
    assert out.tool_call_id == "c1"
    assert len(store) == 0


def test_over_threshold_replaces_content_and_preserves_call_id():
    store = InMemorySpillStore()
    big = "ERROR\n" + ("trace line\n" * 1000)
    msg = ToolMessage(content=big, tool_call_id="call-42")
    out = maybe_spill_tool_message(msg, store, threshold=500)

    assert out.tool_call_id == "call-42"
    assert "[spill:" in str(out.content)
    assert "size=" in str(out.content)
    # Preview must include the early bytes so the model can see the error head.
    assert "ERROR" in str(out.content)
    # Full payload is retrievable from the store.
    assert len(store) == 1


def test_spill_is_idempotent_on_placeholder():
    """Re-spilling an already-spilled message must not double-wrap."""
    store = InMemorySpillStore()
    big = "Z" * 10_000
    msg = ToolMessage(content=big, tool_call_id="c")
    once = maybe_spill_tool_message(msg, store, threshold=500)
    twice = maybe_spill_tool_message(once, store, threshold=500)
    assert once.content == twice.content
    # Store still holds exactly one entry — no re-write of the placeholder text.
    assert len(store) == 1


def test_spill_default_threshold_kicks_in_at_4k():
    """Sanity-check the published default — payloads over 4 KB spill."""
    store = InMemorySpillStore()
    payload = "x" * (DEFAULT_SPILL_THRESHOLD + 1)
    msg = ToolMessage(content=payload, tool_call_id="c")
    out = maybe_spill_tool_message(msg, store)  # no threshold override
    assert "[spill:" in str(out.content)


def test_spill_default_threshold_skips_below_4k():
    store = InMemorySpillStore()
    payload = "x" * DEFAULT_SPILL_THRESHOLD  # exactly at threshold, not over
    msg = ToolMessage(content=payload, tool_call_id="c")
    out = maybe_spill_tool_message(msg, store)
    assert out.content == payload
    assert len(store) == 0


def test_spill_does_not_mutate_input_message():
    """The original ToolMessage is not modified in place."""
    store = InMemorySpillStore()
    big = "y" * 10_000
    msg = ToolMessage(content=big, tool_call_id="c")
    _ = maybe_spill_tool_message(msg, store, threshold=500)
    # Original still has full body.
    assert msg.content == big


def test_spill_handles_list_block_content():
    """ToolMessage content can be a list of dicts (LangChain block format)."""
    store = InMemorySpillStore()
    body_text = "stack-trace " * 1000
    msg = ToolMessage(
        content=[{"type": "text", "text": body_text}],
        tool_call_id="c",
    )
    out = maybe_spill_tool_message(msg, store, threshold=500)
    assert "[spill:" in str(out.content)
    # Stored payload is the stringified body, retrievable.
    assert len(store) == 1


# ---------- spill_messages ----------


def test_spill_messages_only_touches_tool_messages():
    store = InMemorySpillStore()
    big = "k" * 10_000
    msgs = [
        HumanMessage(content="task framing"),
        AIMessage(content="thinking..."),
        ToolMessage(content=big, tool_call_id="c1"),
        AIMessage(content="more thinking"),
        ToolMessage(content="small", tool_call_id="c2"),
    ]
    out = spill_messages(msgs, store, threshold=500)

    assert len(out) == len(msgs)
    # Non-tool messages pass through unchanged (same content).
    assert out[0].content == msgs[0].content
    assert out[1].content == msgs[1].content
    assert out[3].content == msgs[3].content
    # Big tool message spilled.
    assert "[spill:" in str(out[2].content)
    assert out[2].tool_call_id == "c1"
    # Small tool message untouched.
    assert out[4].content == "small"
    # Exactly one payload stored.
    assert len(store) == 1


def test_spill_messages_returns_fresh_list():
    """Caller may mutate the returned list without affecting input."""
    store = InMemorySpillStore()
    src = [ToolMessage(content="a", tool_call_id="x")]
    out = spill_messages(src, store, threshold=1000)
    out.append(HumanMessage(content="extra"))
    assert len(src) == 1
