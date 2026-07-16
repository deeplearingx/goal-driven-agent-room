"""Evaluation harness (EVAL-1).

A small, oracle-agnostic matrix runner that drives every (variant, task) pair
and rolls the results into a pass-rate / cost table. Generalised from the F2
escalation lab; the ablation matrix (EVAL-2) is just a variant set on top.

    from agent_room.eval import BehavioralTask, Variant, run_matrix, to_markdown

The runner stays offline-testable via `memory_graph_builder` (in-memory
checkpointer + fake LLMs); real-provider runs use `sqlite_graph_builder`.
"""

from __future__ import annotations

from agent_room.eval.coding import CodingTask
from agent_room.eval.report import aggregate, to_json, to_markdown
from agent_room.eval.runner import (
    GraphBuilder,
    RunRecord,
    memory_graph_builder,
    run_matrix,
    run_pair,
    sqlite_graph_builder,
)
from agent_room.eval.task import BehavioralTask, Outcome, Task, Verdict
from agent_room.eval.variant import BindingsFactory, Variant, default_bindings

__all__ = [
    "BehavioralTask",
    "BindingsFactory",
    "CodingTask",
    "GraphBuilder",
    "Outcome",
    "RunRecord",
    "Task",
    "Variant",
    "Verdict",
    "aggregate",
    "default_bindings",
    "memory_graph_builder",
    "run_matrix",
    "run_pair",
    "sqlite_graph_builder",
    "to_json",
    "to_markdown",
]
