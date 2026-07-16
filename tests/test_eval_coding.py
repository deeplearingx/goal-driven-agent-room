"""Offline tests for the CodingTask correctness oracle (EVAL-1b).

The oracle is testable without an LLM: seed a sandbox and run `verify()`
directly. The key suite-level guard is `test_seed_tasks_start_red` — every
shipped task must FAIL before any fix, or it isn't measuring anything.
"""

from __future__ import annotations

import pytest

from agent_room.eval import CodingTask
from agent_room.eval.task import Outcome, Verdict
from agent_room.schemas import TaskRequest, TaskResult
from evals import CODING_TASKS


def test_coding_suite_has_portfolio_scale_task_count() -> None:
    assert len(CODING_TASKS) >= 12


def _dummy_outcome() -> Outcome:
    # CodingTask.verify ignores the outcome — it judges the sandbox, not the run.
    return Outcome(
        result=TaskResult(task_id="t", status="completed"),
        escalated=False,
        completed_after_followup=None,
        state_values={},
        error=None,
    )


def _green_task() -> CodingTask:
    return CodingTask(
        name="green",
        request=TaskRequest(title="x", description="y"),
        files={
            "calc.py": "def add(a: int, b: int) -> int:\n    return a + b\n",
            "test_calc.py": "from calc import add\n\n\ndef test_add():\n    assert add(2, 3) == 5\n",
        },
    )


@pytest.mark.asyncio
async def test_verify_passes_on_green_suite() -> None:
    task = _green_task()
    await task.setup()
    try:
        verdict = await task.verify(_dummy_outcome())
    finally:
        await task.teardown()
    assert isinstance(verdict, Verdict)
    assert verdict.passed is True
    assert "exit=0" in verdict.detail


@pytest.mark.asyncio
async def test_verify_fails_on_red_suite() -> None:
    task = CODING_TASKS[0]  # fix_add_subtracts — buggy as seeded
    await task.setup()
    try:
        verdict = await task.verify(_dummy_outcome())
    finally:
        await task.teardown()
    assert verdict.passed is False
    assert "exit=" in verdict.detail


@pytest.mark.asyncio
async def test_registry_pins_four_tools_to_sandbox() -> None:
    task = _green_task()
    with pytest.raises(RuntimeError):
        task.registry()  # before setup
    await task.setup()
    try:
        reg = task.registry()
        assert set(reg.names()) == {"read_text", "write_text", "glob", "shell"}
    finally:
        await task.teardown()


@pytest.mark.asyncio
@pytest.mark.parametrize("task", CODING_TASKS, ids=lambda t: t.name)
async def test_seed_tasks_start_red(task: CodingTask) -> None:
    """Every shipped task must fail before a fix — else it measures nothing."""
    await task.setup()
    try:
        verdict = await task.verify(_dummy_outcome())
    finally:
        await task.teardown()
    assert verdict.passed is False, f"{task.name} passed unfixed — not a live task"
