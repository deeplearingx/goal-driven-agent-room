"""v0.4 §4.3 — SummaryContextEngine: LLM-summarized middle span.

Covers:
- Under-budget: summarizer is NOT called (parent's gate short-circuits).
- Over-budget happy path: summarizer is called once, its text appears in
  the marker between SUMMARY_HEADER and SUMMARY_FOOTER_TEMPLATE.
- Marker shape: single `HumanMessage`, no double-summary, footer has the
  correct omitted count.
- Idempotency: a second `apply` over the result is a no-op.
- Order preservation + ReAct boundary alignment inherit from parent — we
  verify a long ReAct history with parallel tool_calls compresses to
  legal output.
- Failure modes: summarizer raises → fallback to parent's count marker;
  summarizer returns empty content → same fallback.
- Empty-middle edge case: alignment trims everything → no LLM call,
  fallback marker.
- Serialization helper: tool_calls list materialized in input,
  per-message content cap applied.
- Inputs to summarizer: preamble travels as SystemMessage, serialized
  middle as HumanMessage; engine never asks summarizer for tool calls.
"""

from __future__ import annotations

from typing import Any

import pytest
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import (
    AIMessage,
    BaseMessage,
    HumanMessage,
    SystemMessage,
    ToolMessage,
)
from langchain_core.outputs import ChatGeneration, ChatResult

from agent_room.context import SummaryContextEngine
from agent_room.context.summary import (
    DEFAULT_SUMMARY_PREAMBLE,
    SUMMARY_FOOTER_TEMPLATE,
    SUMMARY_HEADER,
    _serialize_for_summary,
)


class _ScriptedSummarizer(BaseChatModel):
    """Records every call and returns scripted responses.

    `responses` is consumed in order. If exhausted, raises so the test
    immediately surfaces an over-call. Set `raise_on_call=Exception(...)`
    to simulate provider failure.
    """

    responses: list[str] = []
    raise_on_call: Exception | None = None

    @property
    def _llm_type(self) -> str:
        return "scripted-summarizer"

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: Any = None,
        **kwargs: Any,
    ) -> ChatResult:
        self._calls.append(list(messages))
        if self.raise_on_call is not None:
            raise self.raise_on_call
        idx = len(self._calls) - 1
        if idx >= len(self.responses):
            raise RuntimeError(
                f"scripted summarizer exhausted at call {idx}; "
                f"only {len(self.responses)} responses scripted"
            )
        text = self.responses[idx]
        return ChatResult(generations=[ChatGeneration(message=AIMessage(content=text))])

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


def _humans(*texts: str) -> list[BaseMessage]:
    return [HumanMessage(content=t) for t in texts]


def _ai_tool(text: str, *, call_id: str) -> AIMessage:
    return AIMessage(
        content=text,
        tool_calls=[{"name": "read_text", "args": {"path": "x.py"}, "id": call_id}],
    )


def _tool(text: str, *, call_id: str) -> ToolMessage:
    return ToolMessage(content=text, tool_call_id=call_id)


# ---------- gate ----------


@pytest.mark.asyncio
async def test_under_budget_does_not_call_summarizer():
    """`should_compress` short-circuits before any summarizer work."""
    summarizer = _ScriptedSummarizer(responses=["unused"])
    eng = SummaryContextEngine(
        summarizer=summarizer, max_messages=5, protect_first_n=1, protect_last_n=2
    )
    src = _humans("a", "b", "c")  # 3 ≤ 5
    out = await eng.apply(src)
    assert [m.content for m in out] == ["a", "b", "c"]
    assert out is not src
    assert summarizer.calls == []  # never invoked


# ---------- happy path ----------


@pytest.mark.asyncio
async def test_compress_inserts_summary_marker():
    summarizer = _ScriptedSummarizer(responses=["worked on auth.py, fixed off-by-one"])
    eng = SummaryContextEngine(
        summarizer=summarizer, max_messages=5, protect_first_n=2, protect_last_n=2
    )
    src = _humans("h0", "h1", "m1", "m2", "m3", "m4", "t1", "t2")  # 8 > 5
    out = await eng.compress(src)

    assert len(out) == 5  # 2 head + 1 marker + 2 tail
    assert [m.content for m in out[:2]] == ["h0", "h1"]
    assert [m.content for m in out[-2:]] == ["t1", "t2"]
    marker = out[2]
    assert isinstance(marker, HumanMessage)
    assert SUMMARY_HEADER in marker.content
    assert "worked on auth.py, fixed off-by-one" in marker.content
    assert SUMMARY_FOOTER_TEMPLATE.format(n=4) in marker.content
    assert len(summarizer.calls) == 1


@pytest.mark.asyncio
async def test_summarizer_receives_preamble_and_serialized_middle():
    """Engine hands [SystemMessage(preamble), HumanMessage(serialized)] to LLM."""
    summarizer = _ScriptedSummarizer(responses=["done"])
    eng = SummaryContextEngine(
        summarizer=summarizer, max_messages=4, protect_first_n=1, protect_last_n=2
    )
    src: list[BaseMessage] = [
        SystemMessage(content="sys"),
        HumanMessage(content="task"),
        AIMessage(content="thinking aloud about middle"),
        HumanMessage(content="follow up"),
        AIMessage(content="last AI"),
        HumanMessage(content="latest human"),
    ]
    await eng.compress(src)

    assert len(summarizer.calls) == 1
    inv = summarizer.calls[0]
    assert len(inv) == 2
    assert isinstance(inv[0], SystemMessage)
    assert inv[0].content == DEFAULT_SUMMARY_PREAMBLE
    assert isinstance(inv[1], HumanMessage)
    assert "TURNS TO SUMMARIZE" in inv[1].content
    # Middle messages (and only middle) appear in the serialized payload.
    # head=[sys], tail=[last AI, latest human], middle=[task, thinking, follow up].
    assert "thinking aloud about middle" in inv[1].content
    assert "task" in inv[1].content
    assert "follow up" in inv[1].content
    # The protected head/tail were NOT given to the summarizer.
    assert "latest human" not in inv[1].content
    assert "last AI" not in inv[1].content
    assert "sys" not in inv[1].content


@pytest.mark.asyncio
async def test_custom_preamble_travels_to_summarizer():
    summarizer = _ScriptedSummarizer(responses=["ok"])
    eng = SummaryContextEngine(
        summarizer=summarizer,
        max_messages=4,
        protect_first_n=1,
        protect_last_n=2,
        preamble="CUSTOM_PREAMBLE_X",
    )
    src = _humans("h", "m1", "m2", "m3", "t1", "t2")
    await eng.compress(src)
    assert summarizer.calls[0][0].content == "CUSTOM_PREAMBLE_X"


# ---------- failure modes ----------


@pytest.mark.asyncio
async def test_summarizer_exception_falls_back_to_count_marker(caplog):
    summarizer = _ScriptedSummarizer(responses=[], raise_on_call=RuntimeError("provider boom"))
    eng = SummaryContextEngine(
        summarizer=summarizer, max_messages=4, protect_first_n=1, protect_last_n=2
    )
    src = _humans("h", "m1", "m2", "m3", "t1", "t2")  # 6 > 4
    import logging

    with caplog.at_level(logging.WARNING, logger="agent_room.context.summary"):
        out = await eng.compress(src)
    # Marker is the parent's count-only marker.
    marker = out[1]
    assert isinstance(marker, HumanMessage)
    assert "messages omitted" in marker.content
    assert SUMMARY_HEADER not in marker.content
    assert any("falling back" in rec.getMessage() for rec in caplog.records)


@pytest.mark.asyncio
async def test_empty_summary_response_falls_back():
    summarizer = _ScriptedSummarizer(responses=["   "])  # whitespace-only
    eng = SummaryContextEngine(
        summarizer=summarizer, max_messages=4, protect_first_n=1, protect_last_n=2
    )
    src = _humans("h", "m1", "m2", "m3", "t1", "t2")
    out = await eng.compress(src)
    marker = out[1]
    assert isinstance(marker, HumanMessage)
    assert "messages omitted" in marker.content
    assert SUMMARY_HEADER not in marker.content


@pytest.mark.asyncio
async def test_empty_middle_skips_summarizer():
    """`_build_marker([])` falls back to count marker without an LLM call.

    `_partition` always produces a non-empty middle when `should_compress`
    is true (alignment moves messages *into* middle, never out), so this
    branch is reached only via direct call. We test it directly to pin
    the defensive fallback behaviour.
    """
    summarizer = _ScriptedSummarizer(responses=[])
    eng = SummaryContextEngine(
        summarizer=summarizer, max_messages=3, protect_first_n=1, protect_last_n=1
    )
    marker = await eng._build_marker([])
    assert isinstance(marker, HumanMessage)
    assert "0 messages omitted" in marker.content
    assert SUMMARY_HEADER not in marker.content
    assert summarizer.calls == []


# ---------- inheritance: parent's invariants still hold ----------


@pytest.mark.asyncio
async def test_idempotency_after_one_compression():
    """Second apply over result is a no-op (parent's invariant)."""
    summarizer = _ScriptedSummarizer(responses=["s1", "s2"])
    eng = SummaryContextEngine(
        summarizer=summarizer, max_messages=5, protect_first_n=1, protect_last_n=2
    )
    src = _humans(*[f"m{i}" for i in range(20)])
    once = await eng.apply(src)
    assert len(once) == 4  # 1 + 1 + 2
    twice = await eng.apply(once)
    assert twice == once
    assert twice is not once
    assert len(summarizer.calls) == 1  # second apply did NOT re-summarize


@pytest.mark.asyncio
async def test_react_boundary_alignment_inherited():
    """Compressed output must never contain orphan ToolMessage at marker boundary."""
    summarizer = _ScriptedSummarizer(responses=["summary text"])
    eng = SummaryContextEngine(
        summarizer=summarizer, max_messages=5, protect_first_n=1, protect_last_n=3
    )
    src: list[BaseMessage] = [
        SystemMessage(content="sys"),
        HumanMessage(content="task"),
        _ai_tool("a", call_id="c-a"),
        _tool("a1", call_id="c-a"),
        _tool("a2", call_id="c-a"),  # tail starts here
        AIMessage(content="done"),
    ]
    out = await eng.compress(src)
    # Walk: every ToolMessage must be preceded by an AIMessage with the
    # matching tool_call.
    pending: dict[str, bool] = {}
    for msg in out:
        if isinstance(msg, AIMessage) and msg.tool_calls:
            for tc in msg.tool_calls:
                pending[tc["id"]] = True
        elif isinstance(msg, ToolMessage):
            assert msg.tool_call_id in pending, (
                f"orphan ToolMessage with id {msg.tool_call_id!r} after summary compression"
            )


@pytest.mark.asyncio
async def test_construction_inherits_parent_invariants():
    """max_messages < protect_first + protect_last + 1 → ValueError."""
    summarizer = _ScriptedSummarizer(responses=[])
    with pytest.raises(ValueError, match="idempotency violation"):
        SummaryContextEngine(
            summarizer=summarizer, max_messages=4, protect_first_n=2, protect_last_n=2
        )


# ---------- serialization helper ----------


def test_serialize_for_summary_includes_role_labels_and_tool_calls():
    msgs: list[BaseMessage] = [
        SystemMessage(content="sys text"),
        HumanMessage(content="user text"),
        _ai_tool("looking up", call_id="c1"),
        _tool("result body", call_id="c1"),
    ]
    out = _serialize_for_summary(msgs)
    assert "[0] SYSTEM" in out
    assert "[1] USER" in out
    assert "[2] ASSISTANT tool_calls=[read_text(" in out
    assert "[3] TOOL tool_call_id='c1'" in out
    assert "result body" in out


def test_serialize_for_summary_caps_per_message_content():
    big = "x" * 10000
    out = _serialize_for_summary([HumanMessage(content=big)])
    assert "[…truncated]" in out
    # 1500 chars + label + ellipsis fits well under 10000
    assert len(out) < 5000
