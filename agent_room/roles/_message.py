"""Helpers shared across role nodes."""

from __future__ import annotations

from typing import Any

from langchain_core.messages import BaseMessage

from agent_room.state import TaskState


def extract_text(message: BaseMessage) -> str:
    """Pull user-visible text out of a chat-model response.

    LangChain's `BaseMessage.content` is `str | list[ContentBlock]`. Some
    providers — notably DeepSeek-V4-Pro served via Anthropic-compatible
    gateways — return a list with a `thinking` block followed by a `text`
    block, like:
        [{"type": "thinking", "thinking": "...", "signature": None},
         {"type": "text",     "text": "the actual answer"}]

    The naive `str(content)` fallback turned that whole list into a Python
    repr, leaking reasoning into stored artifacts. This helper keeps `text`
    blocks (concatenated) and drops `thinking` and other non-display blocks.

    String content is returned as-is.
    """

    content: Any = message.content
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return str(content)

    parts: list[str] = []
    for block in content:
        if isinstance(block, str):
            parts.append(block)
            continue
        if not isinstance(block, dict):
            continue
        if block.get("type") == "text" and isinstance(block.get("text"), str):
            parts.append(block["text"])
        # `thinking`, `tool_use`, `image`, etc. are intentionally dropped from
        # user-visible artifacts. Tool support arrives in v0.3 with its own
        # dedicated state field.
    return "".join(parts)


def format_extra_context(state: TaskState, keys: list[str] | None) -> str:
    """Render `NodeSpec.extra_context_keys` into a HUMAN-message block.

    Each requested key turns into a `# <Key>` section, skipping keys whose
    state value is None / empty / missing. Used by `build_from_spec` to
    feed declared state fields to a node without touching role code.

    The render format is intentionally the same shape role authors already
    use by hand (e.g. `# Plan\n...`) so spec-driven and code-driven prompts
    look identical to the model.
    """

    if not keys:
        return ""

    blocks: list[str] = []
    for key in keys:
        value = state.get(key)
        if value is None:
            continue
        if isinstance(value, list):
            if not value:
                continue
            rendered = "\n".join(f"- {item}" for item in value)
        else:
            rendered = str(value).strip()
            if not rendered:
                continue
        blocks.append(f"# {key.replace('_', ' ').title()}\n{rendered}")
    return "\n\n".join(blocks)
