"""Layered prompt assembler — agent-room v0.4 §4.4.

Borrowed from hermes-agent's `agent/prompt_builder.py` (10-layer system
prompt assembly), reimplemented here per ADR-0007 with a much smaller
surface: 4-5 named layers, no module-level state, no provider routing
inside the assembly code itself.

Why a builder rather than f-strings:
the existing role nodes (`developer`, `reviewer`, `planner_gate`,
`developer_react`) all hand-stitched a list of `prompt_parts`. Each new
context source meant another `if value: parts.append(f"# Header\\n{value}")`
inside the role function. That mixed three concerns:
  1. *what* to include (identity, task, review feedback, extra_context),
  2. *what role* the content plays (system instruction vs. user-side
     context),
  3. *how* to stitch them into LangChain `BaseMessage`s.

The builder splits those: each `Layer` knows what to render and which
role band it lives in; the builder handles ordering and merging.

Shape:
- `Layer(name, role, render)` — a stateless renderer. `render(state)`
  returns `str | None`; `None` means "skip this layer this turn".
- `PromptBuilder(layers).build(state)` — runs every layer, drops the
  `None`s, and groups consecutive same-role outputs into a single
  `BaseMessage`. Two adjacent system layers collapse into one
  `SystemMessage`; two human layers separated by a `None` system layer
  also collapse into one `HumanMessage`.

What this builder deliberately does NOT do:
- No model-routing inside `build()`. Hermes' `_build_system_prompt` has
  ~150 lines of `if "gemini" in model: ...`. That belongs in the layer
  factory (the caller picks which layers to install), not in the
  assembly code. Keep `build()` pure.
- No caching. The system prompt for agent-room nodes is reassembled on
  every entry; LangGraph's checkpoint replay needs deterministic
  output, and the cost of reassembly is negligible (string concat).
- No spill / token accounting. That's the `ContextEngine`'s job (§4.3).
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import Literal

from langchain_core.messages import BaseMessage, HumanMessage, SystemMessage

from agent_room.state import TaskState

LayerRole = Literal["system", "human"]
"""Which message band a layer's output joins.

Two roles for now. `tool` and `assistant` are also valid LangChain
message types but are produced by the LLM / ToolNode, not by the
prompt builder.
"""

LayerRenderer = Callable[[TaskState], "str | None"]
"""A pure function from state to text. `None` means skip this layer."""


@dataclass(frozen=True)
class Layer:
    """A single stateless prompt-section renderer.

    Args:
        name: Short identifier, used in repr and in tests for asserting
            which layers fired. Must be unique within a builder.
        role: Which message band the rendered text joins.
        render: Pure function `(state) -> str | None`. Return `None` to
            skip this layer for this state — the builder won't emit a
            blank section.
    """

    name: str
    role: LayerRole
    render: LayerRenderer

    def __post_init__(self) -> None:
        if self.role not in ("system", "human"):
            raise ValueError(f"Layer.role must be 'system' or 'human', got {self.role!r}")


class PromptBuilder:
    """Compose a list of `Layer`s into a `list[BaseMessage]`.

    Order matters: layers are rendered in declaration order, and
    consecutive same-role outputs are merged. So a builder declared as
    `[system_a, system_b, human_c, system_d, human_e]` produces (assuming
    every layer renders non-None):
        [SystemMessage(a + sep + b), HumanMessage(c), SystemMessage(d), HumanMessage(e)]

    A `None` from any layer is treated as "skip" — it does not break the
    adjacency of its neighbours. So if `system_b` returns `None` above:
        [SystemMessage(a), HumanMessage(c), SystemMessage(d), HumanMessage(e)]

    Layers can be combined across builders via `extend()` for callers
    that want to reuse a base set and append role-specific layers.
    """

    def __init__(self, layers: Iterable[Layer]) -> None:
        self._layers: list[Layer] = list(layers)
        seen: set[str] = set()
        for layer in self._layers:
            if layer.name in seen:
                raise ValueError(f"duplicate layer name: {layer.name!r}")
            seen.add(layer.name)

    @property
    def layers(self) -> list[Layer]:
        """Return a shallow copy of the layer list (read-only view)."""
        return list(self._layers)

    def extend(self, more: Iterable[Layer]) -> PromptBuilder:
        """Return a new builder with extra layers appended.

        Source builder is unchanged. Useful for "take the developer
        preset, then add a memory layer on top" without subclassing.
        """
        return PromptBuilder([*self._layers, *more])

    def build(self, state: TaskState) -> list[BaseMessage]:
        """Render layers and group consecutive same-role outputs.

        Returns an empty list iff every layer returned `None` — that's a
        legal but unusual case and the caller is responsible for
        whatever it means in their context (e.g. a smoke-test state).
        """
        rendered: list[tuple[LayerRole, str]] = []
        for layer in self._layers:
            text = layer.render(state)
            if text is None:
                continue
            stripped = text if isinstance(text, str) else str(text)
            if not stripped.strip():
                # Treat whitespace-only output as "skip" so layer authors
                # can return "" without emitting a blank section.
                continue
            rendered.append((layer.role, stripped))

        if not rendered:
            return []

        return _group_consecutive(rendered)


_SECTION_SEPARATOR = "\n\n"
"""Joiner between adjacent same-role sections.

Two newlines, matching the existing `format_extra_context` separator.
The role nodes' historical output uses single `\\n` between header and
body and `\\n` between sections; the doubled separator here keeps
sections visually distinct after merging.
"""


def _group_consecutive(rendered: list[tuple[LayerRole, str]]) -> list[BaseMessage]:
    """Merge runs of same-role text into single messages."""
    out: list[BaseMessage] = []
    current_role: LayerRole = rendered[0][0]
    current_buf: list[str] = [rendered[0][1]]
    for role, text in rendered[1:]:
        if role == current_role:
            current_buf.append(text)
            continue
        out.append(_emit(current_role, current_buf))
        current_role = role
        current_buf = [text]
    out.append(_emit(current_role, current_buf))
    return out


def _emit(role: LayerRole, parts: list[str]) -> BaseMessage:
    content = _SECTION_SEPARATOR.join(parts)
    if role == "system":
        return SystemMessage(content=content)
    return HumanMessage(content=content)
