"""Model context-window lookup → percent-relative compression budget."""

from __future__ import annotations

import pytest

from agent_room.llm.context_window import (
    _DEFAULT_CONTEXT_WINDOW,
    compression_budget,
    context_window_tokens,
)


def test_exact_and_substring_match() -> None:
    assert context_window_tokens("deepseek-chat") == 64_000
    # Version suffixes resolve to the family entry via substring match.
    assert context_window_tokens("claude-opus-4-8-20260101") == 200_000


def test_unknown_model_falls_back_to_default() -> None:
    assert context_window_tokens("some-future-model-xyz") == _DEFAULT_CONTEXT_WINDOW


def test_budget_is_percent_of_window() -> None:
    assert compression_budget("deepseek-chat", percent=0.5) == 32_000
    assert compression_budget("claude-opus-4-8", percent=0.25) == 50_000


def test_budget_rejects_bad_percent() -> None:
    with pytest.raises(ValueError, match="percent"):
        compression_budget("deepseek-chat", percent=0.0)
    with pytest.raises(ValueError, match="percent"):
        compression_budget("deepseek-chat", percent=1.5)
