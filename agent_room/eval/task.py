"""Eval task abstraction + the correctness oracle seam.

A `Task` pairs a `TaskRequest` with a `verify()` oracle that turns one run's
`Outcome` into a pass/fail `Verdict`. The harness (`runner.py`) is oracle-
agnostic: it drives the run, captures an `Outcome`, and asks the task to judge.

`BehavioralTask` is the v1 oracle — it scores *behaviour* (did the run escalate
when it should have?), re-expressing the F2 escalation lab as a harness task.
The correctness oracle (`CodingTask`: seed a sandbox, run, check pytest) lands
in EVAL-1b and plugs into the same protocol.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Protocol

from agent_room.schemas import TaskRequest, TaskResult

if TYPE_CHECKING:
    from agent_room.tools import Registry


@dataclass(frozen=True, slots=True)
class Outcome:
    """Everything the harness observed from one run, handed to `verify()`."""

    result: TaskResult
    escalated: bool
    completed_after_followup: bool | None
    state_values: Mapping[str, Any]
    error: str | None


@dataclass(frozen=True, slots=True)
class Verdict:
    passed: bool
    detail: str = ""


class Task(Protocol):
    """A unit of evaluation: a request plus an oracle that judges the outcome."""

    name: str
    request: TaskRequest
    follow_up: str | None

    async def setup(self) -> None:
        """Prepare any per-run fixture (e.g. seed a sandbox). Default: no-op."""
        ...

    async def teardown(self) -> None:
        """Tear the fixture down. Always called, even on error. Default: no-op."""
        ...

    def registry(self) -> Registry | None:
        """Tool registry pinned to this task's fixture (e.g. a sandbox), or
        `None` to use the graph's default tools. Read after `setup()`."""
        ...

    async def verify(self, outcome: Outcome) -> Verdict:
        """Turn the observed outcome into pass/fail."""
        ...


@dataclass
class BehavioralTask:
    """Scores *behaviour*: did the run park at `awaiting_user` iff it should?

    This re-expresses an escalation-lab scenario as a harness task. A healthy
    variant escalates under-specified work (`should_escalate=True`) and ships
    fully-specified work without asking (`should_escalate=False`).
    """

    name: str
    request: TaskRequest
    should_escalate: bool
    follow_up: str | None = None
    notes: str = ""
    tags: list[str] = field(default_factory=list)

    async def setup(self) -> None:
        return None

    async def teardown(self) -> None:
        return None

    def registry(self) -> Registry | None:
        return None

    async def verify(self, outcome: Outcome) -> Verdict:
        passed = outcome.escalated == self.should_escalate
        detail = f"escalated={outcome.escalated} expected={self.should_escalate}"
        if outcome.error:
            detail = f"{detail} error={outcome.error}"
        return Verdict(passed=passed, detail=detail)
