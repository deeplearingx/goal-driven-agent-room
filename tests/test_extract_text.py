"""Regression tests for `extract_text`.

Some providers (notably DeepSeek-V4-Pro served via Anthropic-compatible
gateways like Volcano Ark) return a structured content list:

    [{"type": "thinking", "thinking": "...", "signature": None},
     {"type": "text",     "text": "the actual answer"}]

The previous code did `str(content)` which leaked Python repr and reasoning
into stored artifacts. See the v0.1 smoke run on 2026-06-11 — happy_path's
`delivery` field landed in SQLite as `[{'signature': None, 'thinking': ...}]`
which broke the CLI/SSE display path.

These tests pin the contract.
"""

from __future__ import annotations

from langchain_core.messages import AIMessage

from agent_room.roles._message import extract_text


def test_extract_text_str_passthrough():
    msg = AIMessage(content="hello world")
    assert extract_text(msg) == "hello world"


def test_extract_text_drops_thinking_blocks():
    msg = AIMessage(
        content=[
            {"type": "thinking", "thinking": "hidden reasoning", "signature": None},
            {"type": "text", "text": "user-visible answer"},
        ]
    )
    assert extract_text(msg) == "user-visible answer"


def test_extract_text_concatenates_multiple_text_blocks():
    msg = AIMessage(
        content=[
            {"type": "text", "text": "part 1\n"},
            {"type": "thinking", "thinking": "..."},
            {"type": "text", "text": "part 2"},
        ]
    )
    assert extract_text(msg) == "part 1\npart 2"


def test_extract_text_only_thinking_returns_empty():
    msg = AIMessage(content=[{"type": "thinking", "thinking": "no answer was emitted"}])
    assert extract_text(msg) == ""


def test_extract_text_unknown_block_types_dropped():
    msg = AIMessage(
        content=[
            {"type": "tool_use", "name": "calculator", "input": {}},
            {"type": "text", "text": "answer"},
            {"type": "image", "source": {"data": "..."}},
        ]
    )
    assert extract_text(msg) == "answer"


def test_extract_text_handles_string_blocks_in_list():
    msg = AIMessage(content=["hello ", "world"])
    assert extract_text(msg) == "hello world"
