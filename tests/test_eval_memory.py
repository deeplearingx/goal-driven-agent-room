"""Offline mechanics for the memory ablation task (recall behaviour needs a
real LLM; this pins the oracle + seeding)."""

from __future__ import annotations

import pytest

from agent_room.eval.task import Outcome
from agent_room.schemas import TaskResult
from evals.memory_tasks import _SECRET_MARKER, MEMORY_EVAL_DIR, MEMORY_TASKS


def _dummy_outcome() -> Outcome:
    return Outcome(
        result=TaskResult(task_id="t", status="completed"),
        escalated=False,
        completed_after_followup=None,
        state_values={},
        error=None,
    )


@pytest.mark.asyncio
async def test_memory_task_starts_red() -> None:
    task = MEMORY_TASKS[0]
    await task.setup()
    try:
        verdict = await task.verify(_dummy_outcome())
    finally:
        await task.teardown()
    assert verdict.passed is False  # unfixed marker.py raises NotImplementedError


@pytest.mark.asyncio
async def test_memory_task_passes_only_with_exact_marker() -> None:
    task = MEMORY_TASKS[0]
    await task.setup()
    try:
        sandbox = task._require_sandbox()  # noqa: SLF001 — offline mechanics check
        (sandbox / "marker.py").write_text(
            f'def marker() -> str:\n    return "{_SECRET_MARKER}"\n', encoding="utf-8"
        )
        verdict = await task.verify(_dummy_outcome())
    finally:
        await task.teardown()
    assert verdict.passed is True


@pytest.mark.asyncio
async def test_memory_task_seeds_curated_fact() -> None:
    task = MEMORY_TASKS[0]
    await task.setup()
    try:
        seeded = (MEMORY_EVAL_DIR / "memory" / "MEMORY.md").read_text(encoding="utf-8")
        assert _SECRET_MARKER in seeded
    finally:
        await task.teardown()
