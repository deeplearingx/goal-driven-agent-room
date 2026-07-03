"""Ablation variant: a (graph topology + role bindings) configuration.

The escalation lab's `LabVariant` carried only a `GraphSpec` — enough to A/B
graph *topology* (planner_gate vs two_call_review). Ablating the rest of the
system (context engine, memory, transport, per-role model) needs the
`RoleBindings` too, since those live there, not on the spec. `Variant` carries
a `bindings_factory` so each cell of the ablation matrix can configure the full
stack from a single `Settings`.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

from agent_room.config import RoleBindings, Settings
from agent_room.spec import GraphSpec

BindingsFactory = Callable[[Settings | None], RoleBindings]


def default_bindings(settings: Settings | None) -> RoleBindings:
    """Plain env-driven bindings — NoOp context engine and memory."""

    return RoleBindings(settings=settings)


@dataclass
class Variant:
    name: str
    spec: GraphSpec
    bindings_factory: BindingsFactory = default_bindings
    halt_node: str = "reviewer"
    notes: str = ""
    tags: list[str] = field(default_factory=list)
