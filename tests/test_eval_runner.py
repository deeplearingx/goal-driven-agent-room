"""Offline tests for the eval harness (EVAL-1a).

Drives the matrix runner with fake LLMs + in-memory checkpointer, proving the
generalised harness runs end-to-end without a provider and that the behavioural
oracle, aggregation, and markdown report behave.
"""

from __future__ import annotations

import pytest

from agent_room.config import Settings
from agent_room.eval import (
    BehavioralTask,
    Variant,
    aggregate,
    memory_graph_builder,
    run_matrix,
    to_json,
    to_markdown,
)
from agent_room.schemas import ReviewerDecision, TaskRequest
from agent_room.spec import load_preset
from tests.fakes import bindings_with_fakes


def _approving_variant() -> Variant:
    return Variant(
        name="baseline",
        spec=load_preset("full"),
        bindings_factory=lambda _settings: bindings_with_fakes(
            decisions=[ReviewerDecision(decision="approved", feedback="ok", confidence=0.9)],
        ),
    )


def _escalating_variant() -> Variant:
    return Variant(
        name="gate",
        spec=load_preset("full"),
        bindings_factory=lambda _settings: bindings_with_fakes(
            decisions=[
                ReviewerDecision(
                    decision="need_user_decision", feedback="ambiguous", confidence=0.4
                )
            ],
        ),
    )


def _task(name: str, *, should_escalate: bool) -> BehavioralTask:
    return BehavioralTask(
        name=name,
        request=TaskRequest(title=name, description="do the thing", max_revisions=2),
        should_escalate=should_escalate,
    )


@pytest.mark.asyncio
async def test_matrix_runs_offline_and_scores_behaviour() -> None:
    variants = [_approving_variant()]
    tasks = [
        _task("control_specified", should_escalate=False),
        _task("underspec", should_escalate=True),
    ]

    records = await run_matrix(variants, tasks, graph_builder=memory_graph_builder)

    assert len(records) == 2
    by_task = {r.task: r for r in records}
    # Approving variant never escalates → control passes, under-spec fails.
    assert by_task["control_specified"].passed is True
    assert by_task["control_specified"].final_status == "completed"
    assert by_task["control_specified"].escalated is False
    assert by_task["underspec"].passed is False
    assert by_task["underspec"].escalated is False
    assert all(r.elapsed_seconds >= 0 for r in records)


@pytest.mark.asyncio
async def test_escalating_variant_parks_and_oracle_passes() -> None:
    variants = [_escalating_variant()]
    tasks = [_task("underspec", should_escalate=True)]

    records = await run_matrix(variants, tasks, graph_builder=memory_graph_builder)

    (record,) = records
    assert record.escalated is True
    assert record.final_status == "awaiting_user"
    assert record.passed is True
    assert record.decisions == ["need_user_decision"]


@pytest.mark.asyncio
async def test_tokens_are_none_for_fake_llms() -> None:
    records = await run_matrix(
        [_approving_variant()],
        [_task("control_specified", should_escalate=False)],
        graph_builder=memory_graph_builder,
    )
    # Fake LLMs carry no usage_metadata — extraction must stay honest, not zero.
    assert records[0].total_tokens is None
    assert records[0].prompt_tokens is None


@pytest.mark.asyncio
async def test_bindings_factory_receives_settings() -> None:
    seen: list[Settings | None] = []

    def factory(settings: Settings | None):  # type: ignore[no-untyped-def]
        seen.append(settings)
        return bindings_with_fakes(
            decisions=[ReviewerDecision(decision="approved", feedback="ok", confidence=0.9)]
        )

    variant = Variant(name="v", spec=load_preset("full"), bindings_factory=factory)
    sentinel = Settings(default_model="x")
    await run_matrix(
        [variant],
        [_task("t", should_escalate=False)],
        settings=sentinel,
        graph_builder=memory_graph_builder,
    )
    assert seen == [sentinel]


@pytest.mark.asyncio
async def test_aggregate_and_report() -> None:
    variants = [_approving_variant()]
    tasks = [
        _task("control_specified", should_escalate=False),
        _task("underspec", should_escalate=True),
    ]
    records = await run_matrix(variants, tasks, graph_builder=memory_graph_builder)

    agg = aggregate(records)
    assert agg["baseline"]["total"] == 2
    assert agg["baseline"]["passed"] == 1
    assert agg["baseline"]["pass_rate"] == 0.5
    assert agg["baseline"]["tokens_total"] is None  # honest: fakes have no usage

    md = to_markdown(records)
    assert "# eval summary" in md
    assert "baseline" in md
    assert "1/2 (50%)" in md

    blob = to_json(records)
    assert len(blob["rows"]) == 2
    assert "aggregate" in blob
