"""Token-pressure compression trigger (the tokenizer borrow).

Message-count triggering fires on small-but-chatty conversations that fit fine,
adding pure overhead (the docs/context.md real-load finding). Token-budget
triggering fixes that: compress only when the conversation actually approaches
the budget, with an idempotent token-bounded tail.
"""

from __future__ import annotations

import pytest
from langchain_core.messages import HumanMessage, SystemMessage

from agent_room.context import WindowedContextEngine
from agent_room.context.tokens import count_message_tokens, count_tokens


def test_count_tokens_monotonic_and_positive() -> None:
    short = [HumanMessage(content="hi")]
    longer = [HumanMessage(content="word " * 200)]
    assert count_tokens(longer) > count_tokens(short) > 0
    assert count_message_tokens(short[0]) > 0


def test_exactly_one_budget_required() -> None:
    with pytest.raises(ValueError, match="exactly one"):
        WindowedContextEngine()  # neither
    with pytest.raises(ValueError, match="exactly one"):
        WindowedContextEngine(max_messages=8, max_tokens=1000)  # both


def test_target_ratio_validated() -> None:
    with pytest.raises(ValueError, match="target_ratio"):
        WindowedContextEngine(max_tokens=1000, target_ratio=0.0)
    with pytest.raises(ValueError, match="target_ratio"):
        WindowedContextEngine(max_tokens=1000, target_ratio=1.5)


def test_token_trigger_ignores_high_message_count() -> None:
    """The headline fix: 50 tiny messages (high count, low tokens) must NOT
    trigger compression under a token budget."""
    eng = WindowedContextEngine(max_tokens=2000, protect_first_n=1, protect_last_n=2)
    many_small = [HumanMessage(content="ok")] * 50
    assert count_tokens(many_small) < 2000
    assert eng.should_compress(many_small) is False


@pytest.mark.asyncio
async def test_token_compression_engages_and_is_idempotent() -> None:
    eng = WindowedContextEngine(max_tokens=300, protect_first_n=1, protect_last_n=2)
    big = [SystemMessage(content="sys")] + [
        HumanMessage(content=f"chunk {i} " + "word " * 60) for i in range(6)
    ]
    assert eng.should_compress(big) is True

    out = await eng.compress(big)
    # The result must land under budget so it doesn't immediately re-fire.
    assert eng.should_compress(out) is False
    assert len(out) < len(big)
    # Idempotent: compressing the (already-small) result is a no-op.
    assert await eng.compress(out) == out


@pytest.mark.asyncio
async def test_token_mode_preserves_head_and_most_recent() -> None:
    eng = WindowedContextEngine(max_tokens=300, protect_first_n=1, protect_last_n=2)
    msgs = [SystemMessage(content="SYSTEM-PROMPT")] + [
        HumanMessage(content=f"m{i} " + "word " * 60) for i in range(6)
    ]
    out = await eng.compress(msgs)
    assert out[0].content == "SYSTEM-PROMPT"  # protected head kept
    assert out[-1] is msgs[-1]  # most recent turn kept verbatim


@pytest.mark.asyncio
async def test_token_mode_no_middle_returns_unchanged() -> None:
    """When head + tail already cover everything, no marker is added."""
    eng = WindowedContextEngine(max_tokens=1, protect_first_n=1, protect_last_n=2)
    # max_tokens=1 forces should_compress True, but only 2 messages exist →
    # head(1) + tail(<=2) leaves no middle → unchanged.
    msgs = [SystemMessage(content="a"), HumanMessage(content="b")]
    out = await eng.compress(msgs)
    assert out == msgs
