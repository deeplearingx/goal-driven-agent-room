"""Token counting for context-pressure compression triggers.

Uses `tiktoken` (offline, fast) as an *approximate* counter — exact enough for
an "are we near the context window" heuristic, and close enough across providers
(DeepSeek / Anthropic don't ship a public tokenizer; tiktoken's `o200k_base` is
a reasonable proxy for a budget decision). Falls back to a chars/4 estimate when
tiktoken or its encoding is unavailable, so the import is soft.

This is what lets a context engine trigger on *token pressure* rather than a raw
message count — message count fires on small-but-chatty conversations that fit
fine, adding pure overhead (see docs/context.md "Real-load finding").
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from langchain_core.messages import BaseMessage

try:
    import tiktoken

    _ENCODING: Any | None = tiktoken.get_encoding("o200k_base")
except Exception:  # noqa: BLE001 — soft dependency; fall back to estimate
    _ENCODING = None

# Rough per-message overhead for role + framing tokens the encoder doesn't see.
_PER_MESSAGE_OVERHEAD = 4


def _text_of(message: BaseMessage) -> str:
    content = message.content
    if isinstance(content, str):
        return content
    parts: list[str] = []
    for block in content:
        if isinstance(block, str):
            parts.append(block)
        elif isinstance(block, dict):
            parts.append(str(block.get("text", "") or block.get("content", "") or ""))
    return " ".join(parts)


def _encode_len(text: str) -> int:
    if _ENCODING is not None:
        return len(_ENCODING.encode(text))
    return len(text) // 4


def count_message_tokens(message: BaseMessage) -> int:
    """Approximate token count for one message, including any tool-call payload."""

    total = _encode_len(_text_of(message)) + _PER_MESSAGE_OVERHEAD
    tool_calls = getattr(message, "tool_calls", None)
    if tool_calls:
        total += _encode_len(" ".join(str(tc) for tc in tool_calls))
    return total


def count_tokens(messages: Sequence[BaseMessage]) -> int:
    return sum(count_message_tokens(m) for m in messages)
