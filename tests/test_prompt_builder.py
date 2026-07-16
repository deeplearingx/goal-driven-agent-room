"""v0.4 §4.4 — `agent_room.prompt.builder` core: Layer + PromptBuilder.

Covers:
- Layer construction: role validation, immutability.
- Builder uniqueness: duplicate layer names rejected.
- Build with all-None state: returns [] (not [SystemMessage("")]).
- Build with single system layer, single human layer, mixed.
- Same-role merging: two systems → one SystemMessage with `\\n\\n` joiner.
- Skip semantics: None / "" / whitespace-only all suppress emission AND
  do not break adjacency of surrounding layers.
- Layer order is preserved.
- `extend()` returns new builder, leaves original alone.
"""

from __future__ import annotations

import pytest
from langchain_core.messages import HumanMessage, SystemMessage

from agent_room.prompt import Layer, PromptBuilder
from agent_room.prompt.builder import _SECTION_SEPARATOR
from agent_room.state import TaskState


def _state(**kwargs) -> TaskState:
    """Minimal valid TaskState, override fields per test."""
    base = {
        "task_id": "t",
        "title": "T",
        "description": "D",
        "plan": "",
        "code": "",
        "review": None,
        "events": [],
        "artifacts": [],
        "user_directives": [],
        "revision_round": 0,
    }
    base.update(kwargs)
    return base  # type: ignore[return-value]


# ---------- Layer ----------


def test_layer_rejects_unknown_role():
    with pytest.raises(ValueError, match="must be 'system' or 'human'"):
        Layer(name="x", role="assistant", render=lambda _: "x")  # type: ignore[arg-type]


def test_layer_is_frozen():
    """`@dataclass(frozen=True)` raises FrozenInstanceError on attribute set."""
    from dataclasses import FrozenInstanceError

    layer = Layer(name="x", role="system", render=lambda _: "x")
    with pytest.raises(FrozenInstanceError):
        layer.name = "y"  # type: ignore[misc]


# ---------- builder construction ----------


def test_duplicate_layer_names_rejected():
    with pytest.raises(ValueError, match="duplicate layer name: 'a'"):
        PromptBuilder(
            [
                Layer(name="a", role="system", render=lambda _: "x"),
                Layer(name="a", role="human", render=lambda _: "y"),
            ]
        )


def test_layers_property_returns_copy():
    layers = [Layer(name="a", role="system", render=lambda _: "x")]
    builder = PromptBuilder(layers)
    out = builder.layers
    out.append(Layer(name="b", role="human", render=lambda _: "y"))
    # Mutating the returned list does NOT affect the builder.
    assert len(builder.layers) == 1


# ---------- build ----------


def test_build_empty_when_all_layers_skip():
    builder = PromptBuilder(
        [
            Layer(name="a", role="system", render=lambda _: None),
            Layer(name="b", role="human", render=lambda _: ""),
            Layer(name="c", role="human", render=lambda _: "  \n  "),
        ]
    )
    assert builder.build(_state()) == []


def test_build_single_system_layer():
    builder = PromptBuilder([Layer(name="a", role="system", render=lambda _: "S")])
    out = builder.build(_state())
    assert len(out) == 1
    assert isinstance(out[0], SystemMessage)
    assert out[0].content == "S"


def test_build_single_human_layer():
    builder = PromptBuilder([Layer(name="a", role="human", render=lambda _: "H")])
    out = builder.build(_state())
    assert len(out) == 1
    assert isinstance(out[0], HumanMessage)
    assert out[0].content == "H"


def test_build_alternating_roles_keeps_each_separate():
    builder = PromptBuilder(
        [
            Layer(name="s1", role="system", render=lambda _: "S1"),
            Layer(name="h1", role="human", render=lambda _: "H1"),
            Layer(name="s2", role="system", render=lambda _: "S2"),
            Layer(name="h2", role="human", render=lambda _: "H2"),
        ]
    )
    out = builder.build(_state())
    assert [m.content for m in out] == ["S1", "H1", "S2", "H2"]
    assert [type(m).__name__ for m in out] == [
        "SystemMessage",
        "HumanMessage",
        "SystemMessage",
        "HumanMessage",
    ]


def test_consecutive_same_role_layers_merge_with_separator():
    builder = PromptBuilder(
        [
            Layer(name="s1", role="system", render=lambda _: "S1"),
            Layer(name="s2", role="system", render=lambda _: "S2"),
            Layer(name="h1", role="human", render=lambda _: "H1"),
            Layer(name="h2", role="human", render=lambda _: "H2"),
            Layer(name="h3", role="human", render=lambda _: "H3"),
        ]
    )
    out = builder.build(_state())
    assert len(out) == 2
    assert isinstance(out[0], SystemMessage)
    assert out[0].content == f"S1{_SECTION_SEPARATOR}S2"
    assert isinstance(out[1], HumanMessage)
    assert out[1].content == f"H1{_SECTION_SEPARATOR}H2{_SECTION_SEPARATOR}H3"


def test_skipped_layer_does_not_break_adjacency():
    """A None in the middle should still let neighbours merge."""
    builder = PromptBuilder(
        [
            Layer(name="h1", role="human", render=lambda _: "H1"),
            Layer(name="skip", role="human", render=lambda _: None),
            Layer(name="h2", role="human", render=lambda _: "H2"),
        ]
    )
    out = builder.build(_state())
    assert len(out) == 1
    assert isinstance(out[0], HumanMessage)
    assert out[0].content == f"H1{_SECTION_SEPARATOR}H2"


def test_state_passed_to_render():
    """Each layer receives the live state object."""
    seen: list[TaskState] = []

    def capture(state: TaskState) -> str:
        seen.append(state)
        return f"title={state['title']}"

    builder = PromptBuilder([Layer(name="a", role="system", render=capture)])
    out = builder.build(_state(title="X"))
    assert out[0].content == "title=X"
    assert len(seen) == 1


# ---------- extend ----------


def test_extend_returns_new_builder():
    base = PromptBuilder([Layer(name="a", role="system", render=lambda _: "A")])
    extended = base.extend([Layer(name="b", role="human", render=lambda _: "B")])
    assert [layer.name for layer in base.layers] == ["a"]
    assert [layer.name for layer in extended.layers] == ["a", "b"]
    out = extended.build(_state())
    assert [m.content for m in out] == ["A", "B"]


def test_extend_rejects_duplicate_names():
    base = PromptBuilder([Layer(name="a", role="system", render=lambda _: "A")])
    with pytest.raises(ValueError, match="duplicate layer name: 'a'"):
        base.extend([Layer(name="a", role="human", render=lambda _: "X")])
