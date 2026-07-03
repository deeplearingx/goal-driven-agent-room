"""RunManager: detached runs + replayable seq-tagged event buffer (SSE resume)."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

import pytest

from agent_room.budget import BudgetExceededError
from agent_room.server.runs import Frame, RunManager, drain


def _producer(items: list[str], *, delay: float = 0.0):
    async def produce() -> AsyncIterator[Frame]:
        for it in items:
            if delay:
                await asyncio.sleep(delay)
            yield {"event": it, "data": it}

    return produce


@pytest.mark.asyncio
async def test_buffers_and_tags_with_sequential_ids() -> None:
    mgr = RunManager()
    mgr.start("t1", _producer(["a", "b", "c"]))
    frames = await drain(mgr.subscribe("t1", -1))
    assert [f["event"] for f in frames] == ["a", "b", "c"]
    assert [f["id"] for f in frames] == ["0", "1", "2"]


@pytest.mark.asyncio
async def test_replays_only_after_last_event_id() -> None:
    mgr = RunManager()
    run = mgr.start("t1", _producer(["a", "b", "c", "d"]))
    await run.task  # let it finish buffering
    frames = await drain(mgr.subscribe("t1", 1))  # resume after id=1
    assert [f["event"] for f in frames] == ["c", "d"]


@pytest.mark.asyncio
async def test_run_survives_subscriber_cancel() -> None:
    """A subscriber going away (client disconnect) must not stop the run."""
    mgr = RunManager()
    run = mgr.start("t1", _producer(["a", "b", "c"], delay=0.02))
    # Subscribe then bail after the first frame (simulates a dropped connection).
    sub = mgr.subscribe("t1", -1)
    first = await sub.__anext__()
    assert first["event"] == "a"
    await sub.aclose()
    # The detached run keeps going to completion.
    await run.task
    assert run.done
    frames = await drain(mgr.subscribe("t1", -1))
    assert [f["event"] for f in frames] == ["a", "b", "c"]


@pytest.mark.asyncio
async def test_two_subscribers_both_get_all_events() -> None:
    mgr = RunManager()
    mgr.start("t1", _producer(["a", "b"], delay=0.01))
    a, b = await asyncio.gather(drain(mgr.subscribe("t1", -1)), drain(mgr.subscribe("t1", -1)))
    assert [f["event"] for f in a] == ["a", "b"]
    assert [f["event"] for f in b] == ["a", "b"]


@pytest.mark.asyncio
async def test_subscribe_after_done_replays_from_buffer() -> None:
    mgr = RunManager()
    run = mgr.start("t1", _producer(["a", "b"]))
    await run.task
    frames = await drain(mgr.subscribe("t1", -1))
    assert [f["event"] for f in frames] == ["a", "b"]


@pytest.mark.asyncio
async def test_unknown_task_yields_nothing() -> None:
    mgr = RunManager()
    assert mgr.has("nope") is False
    assert await drain(mgr.subscribe("nope", -1)) == []


@pytest.mark.asyncio
async def test_producer_exception_becomes_task_error_frame() -> None:
    async def boom() -> AsyncIterator[Frame]:
        yield {"event": "a", "data": "a"}
        raise RuntimeError("kaboom")

    mgr = RunManager()
    run = mgr.start("t1", boom)
    await run.task
    frames = await drain(mgr.subscribe("t1", -1))
    assert frames[0]["event"] == "a"
    assert frames[-1]["event"] == "task_error"
    assert "kaboom" in frames[-1]["data"]


@pytest.mark.asyncio
async def test_budget_exceeded_becomes_distinct_terminal_frame() -> None:
    """§6.9-2: `BudgetExceededError` from the producer must surface as a
    `task_error` frame carrying `budget_exceeded: true` + which dimension/limit
    tripped — distinct from a generic error, so the UI can show a specific
    reason (mirrors how `cancel()` tags its frame with `cancelled: true`)."""

    async def blows_budget() -> AsyncIterator[Frame]:
        yield {"event": "a", "data": "a"}
        raise BudgetExceededError("max_tokens", 100, 150)

    mgr = RunManager()
    run = mgr.start("t1", blows_budget)
    await run.task
    frames = await drain(mgr.subscribe("t1", -1))
    assert frames[-1]["event"] == "task_error"
    assert '"budget_exceeded": true' in frames[-1]["data"]
    assert '"budget_dimension": "max_tokens"' in frames[-1]["data"]


@pytest.mark.asyncio
async def test_cancel_stops_run_and_emits_terminal_frame() -> None:
    mgr = RunManager()
    started = asyncio.Event()

    async def slow() -> AsyncIterator[Frame]:
        yield {"event": "node_start", "data": "planner"}
        started.set()
        await asyncio.sleep(10)  # long-running; will be cancelled
        yield {"event": "task_finished", "data": "never"}  # pragma: no cover

    run = mgr.start("t1", slow)
    await started.wait()
    assert await mgr.cancel("t1") is True
    assert run.done
    frames = await drain(mgr.subscribe("t1", -1))
    assert frames[0]["event"] == "node_start"
    assert frames[-1]["event"] == "task_error"
    assert "停止" in frames[-1]["data"]


@pytest.mark.asyncio
async def test_cancel_unknown_or_done_returns_false() -> None:
    mgr = RunManager()
    assert await mgr.cancel("nope") is False
    run = mgr.start("t1", _producer(["a"]))
    await run.task
    assert await mgr.cancel("t1") is False  # already done


@pytest.mark.asyncio
async def test_buffer_is_bounded() -> None:
    mgr = RunManager(max_buffer=10)
    run = mgr.start("t1", _producer([str(i) for i in range(50)]))
    await run.task
    # Only the last 10 are retained; ids stay globally sequential.
    frames = await drain(mgr.subscribe("t1", -1))
    assert len(frames) == 10
    assert [f["event"] for f in frames] == [str(i) for i in range(40, 50)]
    assert frames[0]["id"] == "40"
