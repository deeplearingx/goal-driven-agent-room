"""Detached task runs with a replayable event buffer (SSE resume support).

Without this, `POST /tasks/stream` drives the LangGraph run directly from the SSE
generator, so a client disconnect cancels the generator and **aborts the run**.
`RunManager` decouples the two: the run executes in a background task that appends
seq-tagged SSE frames to a per-task buffer, and any number of subscribers can
replay from a `last_event_id` and tail until the run finishes. A dropped
connection no longer kills the task, and a reconnect resumes from the last seq.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Callable
from contextlib import suppress
from dataclasses import dataclass, field
from typing import Any

from agent_room.budget import BudgetExceededError
from agent_room.guardrail import GuardrailTripwire

# Formatted SSE frame: {"event": <type>, "data": <json str>} (+ "id" added here).
Frame = dict[str, Any]
Producer = Callable[[], AsyncIterator[Frame]]

DEFAULT_MAX_BUFFER = 5000
DEFAULT_KEEP_DONE = 32


@dataclass
class _Run:
    task_id: str
    events: list[Frame] = field(default_factory=list)
    done: bool = False
    cond: asyncio.Condition = field(default_factory=asyncio.Condition)
    task: asyncio.Task[None] | None = None
    # seq of the first event still in `events` (climbs when the buffer is trimmed).
    base_seq: int = 0


class RunManager:
    """Owns background runs + their event buffers, keyed by task_id."""

    def __init__(
        self, *, max_buffer: int = DEFAULT_MAX_BUFFER, keep_done: int = DEFAULT_KEEP_DONE
    ) -> None:
        self._runs: dict[str, _Run] = {}
        self._order: list[str] = []
        self._max_buffer = max_buffer
        self._keep_done = keep_done

    def has(self, task_id: str) -> bool:
        return task_id in self._runs

    def start(self, task_id: str, produce: Producer) -> _Run:
        """Launch `produce()` in the background, buffering its frames. Returns the
        handle immediately so the caller can subscribe."""
        self._evict_done()
        run = _Run(task_id=task_id)
        self._runs[task_id] = run
        self._order.append(task_id)
        run.task = asyncio.create_task(self._drive(run, produce))
        return run

    async def _drive(self, run: _Run, produce: Producer) -> None:
        terminal: Frame | None = None
        try:
            async for frame in produce():
                await self._append(run, frame)
        except asyncio.CancelledError:
            # Cooperative stop (user cancel): emit a terminal frame so subscribers
            # see 已停止 rather than a silent close, then finish without re-raising.
            terminal = {"event": "task_error", "data": _cancel_data(run.task_id)}
        except BudgetExceededError as exc:
            # A per-task ceiling (agent_room/budget.py) tripped inside the
            # transport, propagated out of a role node like any other LLM
            # exception. Distinct `budget_exceeded` flag so the UI can show a
            # specific reason instead of a generic failure.
            terminal = {"event": "task_error", "data": _budget_data(run.task_id, exc)}
        except GuardrailTripwire as exc:
            # A `mode="block"` hit at the tool_call / tool_response / output
            # checkpoint (agent_room/guardrail.py; "input" is rejected before
            # the run even starts, in server/api.py). Same propagation shape
            # as BudgetExceededError — the node never catches it.
            terminal = {"event": "task_error", "data": _guardrail_data(run.task_id, exc)}
        except Exception as exc:  # surface as a terminal task_error frame
            terminal = {"event": "task_error", "data": _error_data(run.task_id, str(exc))}
        if terminal is not None:
            await self._append(run, terminal)
        async with run.cond:
            run.done = True
            run.cond.notify_all()

    async def cancel(self, task_id: str) -> bool:
        """Stop a running task: cancel the background run (which stops the graph)
        and emit a terminal frame. Returns False if there's no live/unfinished run."""
        run = self._runs.get(task_id)
        if run is None or run.done:
            return False
        if run.task is not None and not run.task.done():
            run.task.cancel()
            with suppress(asyncio.CancelledError):
                await run.task
        return True

    async def _append(self, run: _Run, frame: Frame) -> None:
        async with run.cond:
            seq = run.base_seq + len(run.events)
            run.events.append({**frame, "id": str(seq)})
            if len(run.events) > self._max_buffer:
                drop = len(run.events) - self._max_buffer
                del run.events[:drop]
                run.base_seq += drop
            run.cond.notify_all()

    async def subscribe(self, task_id: str, last_event_id: int) -> AsyncIterator[Frame]:
        """Yield buffered frames after `last_event_id`, then live ones until done.

        Caller must check `has(task_id)` first; an unknown task yields nothing.
        """
        run = self._runs.get(task_id)
        if run is None:
            return
        idx = max(last_event_id + 1, run.base_seq)
        while True:
            async with run.cond:
                while idx >= run.base_seq + len(run.events) and not run.done:
                    await run.cond.wait()
                start = idx - run.base_seq
                frames = run.events[start:] if start >= 0 else run.events[:]
                done = run.done
                buffered_end = run.base_seq + len(run.events)
            for frame in frames:
                yield frame
            idx = buffered_end
            if done and idx >= buffered_end:
                return

    def _evict_done(self) -> None:
        """Drop the oldest finished runs so memory stays bounded."""
        done_ids = [tid for tid in self._order if (r := self._runs.get(tid)) and r.done]
        while len(done_ids) > self._keep_done:
            victim = done_ids.pop(0)
            self._runs.pop(victim, None)
            self._order.remove(victim)

    async def aclose(self) -> None:
        for run in list(self._runs.values()):
            if run.task and not run.task.done():
                run.task.cancel()
        await asyncio.gather(
            *(r.task for r in self._runs.values() if r.task), return_exceptions=True
        )


def _error_data(task_id: str, error: str) -> str:
    import json

    return json.dumps(
        {"type": "task_error", "task_id": task_id, "error": error}, ensure_ascii=False
    )


def _cancel_data(task_id: str) -> str:
    import json

    return json.dumps(
        {"type": "task_error", "task_id": task_id, "error": "任务已被用户停止", "cancelled": True},
        ensure_ascii=False,
    )


def _budget_data(task_id: str, exc: BudgetExceededError) -> str:
    import json

    return json.dumps(
        {
            "type": "task_error",
            "task_id": task_id,
            "error": str(exc),
            "budget_exceeded": True,
            "budget_dimension": exc.dimension,
            "budget_limit": exc.limit,
            "budget_actual": exc.actual,
        },
        ensure_ascii=False,
    )


def _guardrail_data(task_id: str, exc: GuardrailTripwire) -> str:
    import json

    return json.dumps(
        {
            "type": "task_error",
            "task_id": task_id,
            "error": str(exc),
            "guardrail_blocked": True,
            "guardrail_category": exc.finding.category,
            "guardrail_checkpoint": exc.finding.checkpoint,
        },
        ensure_ascii=False,
    )


async def drain(gen: AsyncIterator[Frame]) -> list[Frame]:
    """Test helper: collect an async frame iterator into a list."""
    out: list[Frame] = []
    async for f in gen:
        out.append(f)
    return out
