"""Per-task token/cost budget enforcement (v1.x §6.9-2 / PLAN.md §6.12).

Consumed by `LangChainTransport` (`agent_room/llm/transport.py`), not part of
it — ADR-0013 caps the transport abstraction itself at "≤3 files, ≤300 lines";
budget tracking is a *consumer* of the transport seam, not the seam.

`Budget` is the immutable ceiling config (server-operator-level; see
`Settings` in `config.py`). `BudgetTracker` is the mutable per-run accumulator:
one instance is shared across every role's `LangChainTransport` call for a
single task run (wired in `graph.py::build_uncompiled_from_spec`). `budget=None`
(the default) makes the tracker a pure NoOp — same default-off shape as
`ContextEngine`/`MemoryProvider`.

Exceeding a limit raises `BudgetExceededError` from inside the transport call,
which propagates out of the role node exactly like any other LLM exception
(nodes don't catch LLM errors — CLAUDE.md §3.4) and is turned into a terminal
`task_error(budget_exceeded=True)` SSE frame by `RunManager` (§6.11's generic
exception handling, no new server-layer logic needed).

**Known scope limit**: only `Transport.invoke()` calls (planner / developer,
including the developer ReAct tool loop / delivery) contribute token usage.
`Transport.structured()` calls (reviewer / planner_gate / reviewer_two_call) do
not — LangChain's `with_structured_output` doesn't surface `usage_metadata` on
the path this transport uses, so there's nothing to record. `max_tool_calls`
enforcement still covers the developer's tool loop regardless.
"""

from __future__ import annotations

from dataclasses import dataclass

from agent_room.llm.transport import Usage


@dataclass(frozen=True, slots=True)
class Budget:
    """Per-task ceiling. Every field is optional; unset = unlimited for that
    dimension. `price_per_1k_*` only matter when `max_cost_usd` is set."""

    max_tokens: int | None = None
    max_tool_calls: int | None = None
    max_cost_usd: float | None = None
    price_per_1k_input: float | None = None
    price_per_1k_output: float | None = None


class BudgetExceededError(RuntimeError):
    """Raised by the transport when a call would push cumulative usage over
    a configured ceiling. `dimension` is one of "max_tokens" / "max_tool_calls"
    / "max_cost_usd" for callers that want to branch on which limit tripped."""

    def __init__(self, dimension: str, limit: float, actual: float) -> None:
        self.dimension = dimension
        self.limit = limit
        self.actual = actual
        super().__init__(f"budget exceeded: {dimension} limit={limit!r} actual={actual!r}")


class BudgetTracker:
    """Mutable per-task accumulator. Not thread-safe by design — one tracker
    is built per compiled graph (one task run), consumed sequentially by
    whichever role node is currently executing."""

    def __init__(self, budget: Budget | None = None) -> None:
        self.budget = budget
        self.total_tokens = 0
        self.tool_calls = 0
        self.cost_usd = 0.0

    def record(self, usage: Usage | None) -> None:
        """Accumulate `usage` and raise if any configured ceiling is now
        exceeded. `usage=None` is a no-op accumulation (nothing added, ceilings
        already tripped by prior calls still raise) — kept for callers that
        may not always have usage data available."""
        budget = self.budget
        if budget is None:
            return
        if usage is not None:
            self.total_tokens += usage.total_tokens
            if budget.price_per_1k_input is not None:
                self.cost_usd += usage.prompt_tokens / 1000 * budget.price_per_1k_input
            if budget.price_per_1k_output is not None:
                self.cost_usd += usage.completion_tokens / 1000 * budget.price_per_1k_output
        if budget.max_tokens is not None and self.total_tokens > budget.max_tokens:
            raise BudgetExceededError("max_tokens", budget.max_tokens, self.total_tokens)
        if budget.max_cost_usd is not None and self.cost_usd > budget.max_cost_usd:
            raise BudgetExceededError("max_cost_usd", budget.max_cost_usd, round(self.cost_usd, 6))

    def record_tool_calls(self, count: int) -> None:
        """Call after a response that requested `count` tool calls. Raises if
        the running total now exceeds `max_tool_calls`."""
        budget = self.budget
        if budget is None or count <= 0:
            return
        self.tool_calls += count
        if budget.max_tool_calls is not None and self.tool_calls > budget.max_tool_calls:
            raise BudgetExceededError("max_tool_calls", budget.max_tool_calls, self.tool_calls)


def describe_budget(budget: Budget | None) -> dict[str, object]:
    """Operator-facing summary of the active ceiling (surfaced via /healthz),
    mirroring `react_runtime.describe_tool_envelope`'s shape."""
    if budget is None:
        return {
            "enabled": False,
            "max_tokens": None,
            "max_tool_calls": None,
            "max_cost_usd": None,
        }
    return {
        "enabled": True,
        "max_tokens": budget.max_tokens,
        "max_tool_calls": budget.max_tool_calls,
        "max_cost_usd": budget.max_cost_usd,
        "price_per_1k_input": budget.price_per_1k_input,
        "price_per_1k_output": budget.price_per_1k_output,
    }
