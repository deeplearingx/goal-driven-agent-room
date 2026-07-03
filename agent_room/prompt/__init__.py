"""Layered prompt assembly — agent-room v0.4 §4.4.

`PromptBuilder` composes a list of named `Layer`s into a `list[BaseMessage]`,
with consecutive same-role layers merged. Borrowed shape from hermes-agent's
`agent/prompt_builder.py`, reimplemented per ADR-0007 with a much smaller
surface (4-5 layers, no model-routing inside `build()`).

Public API:

- `Layer(name, role, render)` — stateless renderer.
- `PromptBuilder(layers).build(state)` — render + group.
- `developer_prompt_builder(...)` — preset producing the same
  `[SystemMessage, HumanMessage]` shape that the developer role used
  before the refactor.
"""

from __future__ import annotations

from agent_room.prompt.builder import Layer, PromptBuilder
from agent_room.prompt.developer import (
    DEVELOPER_SYSTEM,
    DEVELOPER_SYSTEM_WITH_TOOLS,
    developer_prompt_builder,
)

__all__ = [
    "DEVELOPER_SYSTEM",
    "DEVELOPER_SYSTEM_WITH_TOOLS",
    "Layer",
    "PromptBuilder",
    "developer_prompt_builder",
]
