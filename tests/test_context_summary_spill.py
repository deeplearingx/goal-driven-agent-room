"""v0.4 §3.4 — SummaryContextEngine + spill integration tests.

When a developer ReAct loop hits a long shell command or a wide glob, the
resulting `ToolMessage` can be tens of KB. Without spill, that body lands
in the summarizer's prompt and we pay full token cost to summarize a
stack trace as "the build failed".

These tests pin the integration:

1. With `spill_store=None`, behaviour is exactly v0.4 §4.3 — the summarizer
   sees raw tool bodies (capped only by `_SERIALIZE_CONTENT_CAP`).
2. With a `spill_store` wired in, big `ToolMessage`s are replaced by
   placeholders BEFORE serialization, so the summarizer's prompt drops
   to the placeholder size.
3. The spill store gets the full body — nothing is lost; spill is a
   prompt-budget optimization, not data deletion.
4. Small tool messages stay raw (under threshold) — no unnecessary spill.
5. Backwards compat: existing `SummaryContextEngine` callers that don't
   pass `spill_store` keep working; the parameter is keyword-only with
   a `None` default.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

import pytest
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import (
    AIMessage,
    BaseMessage,
    HumanMessage,
    ToolMessage,
)
from langchain_core.outputs import ChatGeneration, ChatResult

from agent_room.context import SummaryContextEngine
from agent_room.tools.spill import InMemorySpillStore


class _CapturingSummarizer(BaseChatModel):
    """Records the exact serialized prompt the summarizer sees."""

    @property
    def _llm_type(self) -> str:
        return "spill-test-summarizer"

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: Any = None,
        **kwargs: Any,
    ) -> ChatResult:
        self._calls.append(list(messages))
        return ChatResult(generations=[ChatGeneration(message=AIMessage(content="dense summary"))])

    async def _agenerate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: Any = None,
        **kwargs: Any,
    ) -> ChatResult:
        return self._generate(messages, stop=stop, run_manager=run_manager, **kwargs)

    def model_post_init(self, __ctx: Any) -> None:
        object.__setattr__(self, "_calls", [])

    @property
    def calls(self) -> list[list[BaseMessage]]:
        return self._calls

    @property
    def last_user_prompt(self) -> str:
        """The serialized middle the summarizer was asked to compress."""
        last = self._calls[-1]
        # Prompt shape is [SystemMessage(preamble), HumanMessage(serialized)].
        return str(last[1].content)


def _build_history_with_one_huge_tool_message(*, big_size: int = 30_000) -> Sequence[BaseMessage]:
    """Build an 8-message history where one ToolMessage carries a huge body.

    Shape: [Sys, Human, AI(tc), HUGE Tool, AI(tc), small Tool, AI, Human]
    Length 8, with `max_messages=5, protect_first=2, protect_last=2` the
    middle (msgs 2..5) gets dropped and serialized for the summarizer.
    The huge ToolMessage is in the middle by construction.
    """
    return [
        HumanMessage(content="SYSTEM: planner brief"),  # head[0]
        HumanMessage(content="initial framing"),  # head[1]
        AIMessage(
            content="",
            tool_calls=[
                {"id": "c1", "name": "shell", "args": {"cmd": "pytest"}, "type": "tool_call"}
            ],
        ),
        ToolMessage(
            content="ERROR: test_foo failed\n" + ("trace line\n" * (big_size // 11)),
            tool_call_id="c1",
        ),
        AIMessage(
            content="",
            tool_calls=[
                {"id": "c2", "name": "read_text", "args": {"path": "x.py"}, "type": "tool_call"}
            ],
        ),
        ToolMessage(content="small file content", tool_call_id="c2"),
        AIMessage(content="reasoning..."),  # tail[0]
        HumanMessage(content="reviewer feedback"),  # tail[1]
    ]


@pytest.mark.asyncio
async def test_no_spill_store_preserves_v04_behaviour():
    """`spill_store=None` (default) — summarizer sees raw bodies, just like v0.4 §4.3."""
    summarizer = _CapturingSummarizer()
    engine = SummaryContextEngine(
        summarizer=summarizer,
        max_messages=5,
        protect_first_n=2,
        protect_last_n=2,
        # spill_store omitted on purpose
    )
    src = _build_history_with_one_huge_tool_message(big_size=10_000)
    await engine.compress(src)

    serialized = summarizer.last_user_prompt
    # The huge body is serialized (truncated at the per-message cap, but
    # the trace lines themselves are present, NOT a spill placeholder).
    assert "[spill:" not in serialized
    assert "trace line" in serialized


@pytest.mark.asyncio
async def test_spill_store_replaces_big_tool_messages_in_summary_prompt():
    """With a store wired in, the huge body is replaced by `[spill: ref=...]`
    before the summarizer ever sees it."""
    summarizer = _CapturingSummarizer()
    store = InMemorySpillStore()
    engine = SummaryContextEngine(
        summarizer=summarizer,
        max_messages=5,
        protect_first_n=2,
        protect_last_n=2,
        spill_store=store,
        spill_threshold=1_000,
    )
    src = _build_history_with_one_huge_tool_message(big_size=20_000)
    await engine.compress(src)

    serialized = summarizer.last_user_prompt
    assert "[spill:" in serialized
    assert "size=" in serialized
    # The placeholder preview is short — the summarizer's prompt is now
    # bounded by placeholder length, not by raw body length.
    # Sanity: serialized prompt should be much smaller than the raw body.
    assert len(serialized) < 5_000  # raw body alone was ~20K + format overhead


@pytest.mark.asyncio
async def test_spill_store_preserves_full_payload_for_later_retrieval():
    """The body isn't lost — full content lives in the store, retrievable by ref."""
    summarizer = _CapturingSummarizer()
    store = InMemorySpillStore()
    engine = SummaryContextEngine(
        summarizer=summarizer,
        max_messages=5,
        protect_first_n=2,
        protect_last_n=2,
        spill_store=store,
        spill_threshold=1_000,
    )
    src = _build_history_with_one_huge_tool_message(big_size=15_000)
    await engine.compress(src)

    # Exactly one payload spilled (the huge ToolMessage).
    assert len(store) == 1
    # Pull the ref out of the serialized prompt and verify the full body
    # is recoverable.
    serialized = summarizer.last_user_prompt
    ref_token = _extract_first_spill_ref(serialized)
    payload = store.get(ref_token)
    assert payload is not None
    assert "ERROR: test_foo failed" in payload
    # Full body intact, not a preview.
    assert len(payload) >= 15_000


@pytest.mark.asyncio
async def test_small_tool_messages_not_spilled_even_with_store():
    """Sub-threshold ToolMessages stay raw — spill is opt-in by size."""
    summarizer = _CapturingSummarizer()
    store = InMemorySpillStore()
    engine = SummaryContextEngine(
        summarizer=summarizer,
        max_messages=5,
        protect_first_n=2,
        protect_last_n=2,
        spill_store=store,
        spill_threshold=10_000,  # high threshold
    )
    src = _build_history_with_one_huge_tool_message(big_size=5_000)  # under threshold
    await engine.compress(src)

    # Nothing spilled — every tool message was under threshold.
    assert len(store) == 0
    serialized = summarizer.last_user_prompt
    assert "[spill:" not in serialized


@pytest.mark.asyncio
async def test_spill_does_not_break_ai_tool_call_pairing_in_serialization():
    """The placeholder ToolMessage keeps `tool_call_id`, so the
    serialized middle still pairs AIMessage(tool_call) → ToolMessage(...).
    Important because we surface tool_call_id in the serialization."""
    summarizer = _CapturingSummarizer()
    store = InMemorySpillStore()
    engine = SummaryContextEngine(
        summarizer=summarizer,
        max_messages=5,
        protect_first_n=2,
        protect_last_n=2,
        spill_store=store,
        spill_threshold=500,
    )
    src = _build_history_with_one_huge_tool_message(big_size=10_000)
    await engine.compress(src)

    serialized = summarizer.last_user_prompt
    # The serializer surfaces tool_call_id for ToolMessages — it should
    # still be 'c1' on the spilled one.
    assert "tool_call_id='c1'" in serialized


@pytest.mark.asyncio
async def test_spill_does_not_change_marker_shape_in_compressed_output():
    """The compression result shape is unchanged — head + 1 marker + tail.
    Spill only affects what the summarizer ingests, not what the engine
    emits to the developer LLM."""
    summarizer = _CapturingSummarizer()
    store = InMemorySpillStore()
    engine = SummaryContextEngine(
        summarizer=summarizer,
        max_messages=5,
        protect_first_n=2,
        protect_last_n=2,
        spill_store=store,
        spill_threshold=1_000,
    )
    src = _build_history_with_one_huge_tool_message(big_size=20_000)
    out = await engine.compress(src)

    # Same shape as without spill: head[0..2] + marker + tail[-2:].
    assert len(out) == 5
    assert isinstance(out[2], HumanMessage)
    body = str(out[2].content)
    # The marker is the LLM's "dense summary" wrapped in SUMMARY_HEADER/FOOTER.
    assert "earlier turns compacted" in body
    assert "dense summary" in body
    # Note: the ENGINE's output marker does NOT contain `[spill:`.
    # Spill placeholders only show up in the summarizer's input.
    assert "[spill:" not in body


def _extract_first_spill_ref(text: str) -> str:
    """Pull the first `[spill:<ref> size=...]` ref token out of `text`."""
    import re

    m = re.search(r"\[spill:([0-9a-f]+)\s+size=", text)
    assert m, f"no spill ref token found in: {text[:200]!r}..."
    return m.group(1)
