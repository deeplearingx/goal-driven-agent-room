"""BudgetTracker: per-task token/cost/tool-call ceiling enforcement (§6.9-2).

`budget=None` must be a pure NoOp (matches ContextEngine/MemoryProvider's
default-off shape); a configured `Budget` must raise `BudgetExceededError`
exactly when a call pushes the running total over its ceiling, and report the
dimension/limit/actual that tripped.
"""

from __future__ import annotations

import pytest

from agent_room.budget import Budget, BudgetExceededError, BudgetTracker, describe_budget
from agent_room.llm.transport import Usage


def _usage(total: int, *, prompt: int | None = None, completion: int | None = None) -> Usage:
    return Usage(
        prompt_tokens=prompt if prompt is not None else total,
        completion_tokens=completion if completion is not None else 0,
        total_tokens=total,
    )


def test_noop_when_budget_none() -> None:
    tracker = BudgetTracker(None)
    tracker.record(_usage(10_000_000))  # no ceiling configured, never raises
    tracker.record_tool_calls(10_000)
    assert tracker.total_tokens == 0  # NoOp doesn't even accumulate
    assert tracker.tool_calls == 0


def test_max_tokens_accumulates_and_raises_when_exceeded() -> None:
    tracker = BudgetTracker(Budget(max_tokens=100))
    tracker.record(_usage(60))
    assert tracker.total_tokens == 60
    with pytest.raises(BudgetExceededError) as exc_info:
        tracker.record(_usage(60))  # 60 + 60 = 120 > 100
    assert exc_info.value.dimension == "max_tokens"
    assert exc_info.value.limit == 100
    assert exc_info.value.actual == 120


def test_max_tokens_exactly_at_limit_does_not_raise() -> None:
    tracker = BudgetTracker(Budget(max_tokens=100))
    tracker.record(_usage(100))  # exactly at the ceiling — not over it
    assert tracker.total_tokens == 100


def test_record_none_adds_nothing_but_still_checks() -> None:
    """`usage=None` accumulates zero tokens, but a ceiling already tripped by a
    prior call still raises (the crossing call itself always raises first, so
    in practice this only matters if the tracker is queried again post-raise)."""
    tracker = BudgetTracker(Budget(max_tokens=100))
    tracker.record(_usage(40))
    tracker.record(None)  # no-op accumulation, still under budget — no raise
    assert tracker.total_tokens == 40


def test_max_tool_calls_raises_on_overage() -> None:
    tracker = BudgetTracker(Budget(max_tool_calls=2))
    tracker.record_tool_calls(2)
    assert tracker.tool_calls == 2
    with pytest.raises(BudgetExceededError) as exc_info:
        tracker.record_tool_calls(1)
    assert exc_info.value.dimension == "max_tool_calls"


def test_record_tool_calls_zero_or_negative_is_noop() -> None:
    tracker = BudgetTracker(Budget(max_tool_calls=1))
    tracker.record_tool_calls(0)
    assert tracker.tool_calls == 0


def test_max_cost_usd_computed_from_price_per_1k() -> None:
    tracker = BudgetTracker(
        Budget(max_cost_usd=0.01, price_per_1k_input=1.0, price_per_1k_output=2.0)
    )
    # 5000 prompt tokens * $1/1k = $5... too big; use smaller numbers.
    tracker.record(_usage(0, prompt=5, completion=0))  # 5/1000 * 1.0 = 0.005
    assert tracker.cost_usd == pytest.approx(0.005)
    with pytest.raises(BudgetExceededError) as exc_info:
        tracker.record(_usage(0, prompt=0, completion=10))  # +10/1000*2.0=0.02 -> 0.025 > 0.01
    assert exc_info.value.dimension == "max_cost_usd"


def test_max_cost_without_price_config_never_accrues() -> None:
    """max_cost_usd is set but no price_per_1k_* — cost stays 0, never raises
    (an operator half-configuring cost budget shouldn't silently block everything)."""
    tracker = BudgetTracker(Budget(max_cost_usd=0.0001))
    tracker.record(_usage(1_000_000))
    assert tracker.cost_usd == 0.0


def test_describe_budget_disabled() -> None:
    assert describe_budget(None) == {
        "enabled": False,
        "max_tokens": None,
        "max_tool_calls": None,
        "max_cost_usd": None,
    }


def test_describe_budget_enabled() -> None:
    env = describe_budget(Budget(max_tokens=500, max_tool_calls=10))
    assert env["enabled"] is True
    assert env["max_tokens"] == 500
    assert env["max_tool_calls"] == 10
