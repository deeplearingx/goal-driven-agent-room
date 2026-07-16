"""Variant definitions: each is a (name, GraphSpec, halt_node) triple.

`halt_node` is what `service.resume(at_node=...)` should pass when the
variant escalates — `planner_gate` parks at the planner; the others park
after the reviewer.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

from agent_room.spec import GraphSpec, load_preset


@dataclass
class LabVariant:
    name: str
    spec: GraphSpec
    halt_node: str
    notes: str = ""


def model_swap_spec(reviewer_model: str) -> GraphSpec:
    """Baseline pipeline, but the reviewer node uses a different model id."""

    spec = load_preset("full")
    # GraphSpec is a Pydantic model; mutate via copy + assign to keep validators on.
    nodes = dict(spec.nodes)
    reviewer = nodes["reviewer"].model_copy(update={"model": reviewer_model})
    nodes["reviewer"] = reviewer
    return spec.model_copy(update={"name": f"model_swap_{reviewer_model}", "nodes": nodes})


def build_variants() -> list[LabVariant]:
    variants = [
        LabVariant(
            name="baseline",
            spec=load_preset("full"),
            halt_node="reviewer",
            notes="v0.1 single-call reviewer; the F2 finding's baseline.",
        ),
        LabVariant(
            name="planner_gate",
            spec=load_preset("planner_gate"),
            halt_node="planner",
            notes="Planner emits open_questions; gate halts before developer.",
        ),
        LabVariant(
            name="two_call_review",
            spec=load_preset("two_call_review"),
            halt_node="reviewer",
            notes="Reviewer focus-check then full review; under-spec → escalate.",
        ),
    ]

    swap_model = os.getenv("AGENT_ROOM_LAB_REVIEWER_MODEL")
    if swap_model:
        variants.append(
            LabVariant(
                name=f"model_swap_{swap_model}",
                spec=model_swap_spec(swap_model),
                halt_node="reviewer",
                notes=f"Baseline pipeline with reviewer pinned to {swap_model!r}.",
            )
        )

    return variants


def filter_variants(variants: list[LabVariant], names: list[str] | None) -> list[LabVariant]:
    if not names:
        return variants
    keep = set(names)
    return [v for v in variants if v.name in keep]
