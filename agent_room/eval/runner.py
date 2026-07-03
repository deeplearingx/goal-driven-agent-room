"""Matrix runner: drive every (variant, task) pair and record telemetry.

Generalised from `examples/escalation_lab/run.py`. The lab scored a single
behavioural axis (escalate-when-you-should); this runner is oracle-agnostic —
each `Task` brings its own `verify()`. Graph construction is injected via a
`GraphBuilder` so the same runner serves offline tests (`memory_graph_builder`,
in-memory checkpointer + fake LLMs) and real-provider runs
(`sqlite_graph_builder`).

The graph is built per (variant, task) pair, not per variant: a `CodingTask`
pins its tools to a per-run sandbox via `task.registry()`, so the topology a
variant describes must be instantiated against *that task's* tools. Compiling a
graph is microseconds next to the LLM calls it drives — the per-pair build is
free in practice and removes the variant/task sandbox coupling.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable, Mapping, Sequence
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from dataclasses import dataclass, field
from typing import Any

from langchain_core.callbacks import get_usage_metadata_callback

from agent_room.config import RoleBindings, Settings
from agent_room.eval.task import Outcome, Task
from agent_room.eval.variant import Variant
from agent_room.graph import build_from_spec, build_with_sqlite_checkpointer
from agent_room.schemas import TaskResult
from agent_room.service import AgentRoomService
from agent_room.spec import GraphSpec
from agent_room.tools import Registry

GraphCM = AbstractAsyncContextManager[Any]
GraphBuilder = Callable[[GraphSpec, RoleBindings, Registry | None], GraphCM]


@dataclass
class RunRecord:
    variant: str
    task: str
    task_id: str
    passed: bool
    verdict_detail: str
    final_status: str
    rounds: int
    decisions: list[str]
    elapsed_seconds: float
    escalated: bool
    completed_after_followup: bool | None
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    total_tokens: int | None = None
    error: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)


@asynccontextmanager
async def _memory_cm(spec: GraphSpec, bindings: RoleBindings, registry: Registry | None) -> Any:
    yield build_from_spec(spec, bindings, registry=registry)


def memory_graph_builder(
    spec: GraphSpec, bindings: RoleBindings, registry: Registry | None
) -> GraphCM:
    """In-memory checkpointer — for offline tests with fake LLMs."""

    return _memory_cm(spec, bindings, registry)


def sqlite_graph_builder(db_path: str) -> GraphBuilder:
    """SQLite-backed checkpointer — for real-provider lab runs."""

    def _build(spec: GraphSpec, bindings: RoleBindings, registry: Registry | None) -> GraphCM:
        return build_with_sqlite_checkpointer(
            bindings, db_path=db_path, spec=spec, registry=registry
        )

    return _build


def _decision_of(result: TaskResult) -> str:
    if result.review is None:
        return "none"
    return result.review.decision


def _sum_usage(
    usage_by_model: Mapping[str, Any],
) -> tuple[int | None, int | None, int | None]:
    """Sum token usage across every model a run touched.

    Fed by a `get_usage_metadata_callback()` context that captures every
    chat-model call, so this covers plain pipeline nodes too — not only the
    ReAct path whose AIMessages land in state (the EVAL-1c gap). Returns an
    all-`None` triple when nothing reported usage (e.g. fake LLMs offline).
    """

    if not usage_by_model:
        return None, None, None
    prompt = sum(int(u.get("input_tokens", 0) or 0) for u in usage_by_model.values())
    completion = sum(int(u.get("output_tokens", 0) or 0) for u in usage_by_model.values())
    total = sum(int(u.get("total_tokens", 0) or 0) for u in usage_by_model.values())
    return prompt, completion, total


async def _state_values(service: AgentRoomService, task_id: str) -> dict[str, Any]:
    state = await service.graph.aget_state({"configurable": {"thread_id": task_id}})
    return dict(state.values or {})


async def run_pair(
    variant: Variant,
    task: Task,
    *,
    bindings: RoleBindings,
    graph_builder: GraphBuilder = memory_graph_builder,
    timeout_s: float = 600.0,
) -> RunRecord:
    """Run one (variant, task) cell: setup → build → run → (resume) → verify → teardown."""

    task_id = AgentRoomService.new_task_id()
    decisions: list[str] = []
    error: str | None = None
    completed_after_followup: bool | None = None
    start = time.perf_counter()

    await task.setup()
    try:
        with get_usage_metadata_callback() as usage_cb:
            async with graph_builder(variant.spec, bindings, task.registry()) as graph:
                service = AgentRoomService(graph)
                try:
                    snap = await asyncio.wait_for(
                        service.run(task.request, task_id=task_id), timeout=timeout_s
                    )
                    decisions.append(_decision_of(snap))
                    escalated = snap.status == "awaiting_user"
                    if escalated and task.follow_up is not None:
                        snap = await asyncio.wait_for(
                            service.resume(task_id, task.follow_up, at_node=variant.halt_node),
                            timeout=timeout_s,
                        )
                        decisions.append(_decision_of(snap))
                        completed_after_followup = snap.status == "completed"
                except TimeoutError:
                    error = f"timeout after {timeout_s}s"
                    snap = await service.snapshot(task_id)
                    escalated = snap.status == "awaiting_user"
                except Exception as exc:  # noqa: BLE001 — harness records, never raises
                    error = f"{type(exc).__name__}: {exc}"
                    snap = await service.snapshot(task_id)
                    escalated = snap.status == "awaiting_user"

                elapsed = time.perf_counter() - start
                values = await _state_values(service, task_id)
                outcome = Outcome(
                    result=snap,
                    escalated=escalated,
                    completed_after_followup=completed_after_followup,
                    state_values=values,
                    error=error,
                )
                verdict = await task.verify(outcome)
    finally:
        await task.teardown()

    prompt_tokens, completion_tokens, total_tokens = _sum_usage(usage_cb.usage_metadata)
    return RunRecord(
        variant=variant.name,
        task=task.name,
        task_id=task_id,
        passed=verdict.passed,
        verdict_detail=verdict.detail,
        final_status=snap.status,
        rounds=int(values.get("revision_round", 0) or 0),
        decisions=decisions,
        elapsed_seconds=round(elapsed, 3),
        escalated=escalated,
        completed_after_followup=completed_after_followup,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        total_tokens=total_tokens,
        error=error,
        # ReAct history length (append-only, unaffected by compression) — tells
        # whether a context engine could have engaged (> its max_messages).
        # verify_round: goal-mode oracle iterations (0 on graphs without a verifier).
        extra={
            "dev_messages": len(values.get("dev_messages") or []),
            "verify_round": int(values.get("verify_round", 0) or 0),
        },
    )


async def run_matrix(
    variants: Sequence[Variant],
    tasks: Sequence[Task],
    *,
    settings: Settings | None = None,
    graph_builder: GraphBuilder = memory_graph_builder,
    timeout_s: float = 600.0,
) -> list[RunRecord]:
    """Drive the full (variant × task) matrix serially; bindings built per variant."""

    records: list[RunRecord] = []
    for variant in variants:
        bindings = variant.bindings_factory(settings)
        for task in tasks:
            records.append(
                await run_pair(
                    variant,
                    task,
                    bindings=bindings,
                    graph_builder=graph_builder,
                    timeout_s=timeout_s,
                )
            )
    return records
