"""Offline tests for the escalation-lab aggregation.

The lab itself hits a real LLM and is run by hand; these tests just check
that `_aggregate` and the markdown renderer don't drift, so a future
refactor doesn't silently break the analysis.
"""

from __future__ import annotations

from examples.escalation_lab.run import RunRow, _aggregate, _markdown_summary


def _row(
    variant: str,
    scenario: str,
    *,
    escalated: bool,
    should: bool,
    error: str | None = None,
    completed_after: bool | None = None,
    rounds: int = 1,
    wall: float = 1.0,
) -> RunRow:
    return RunRow(
        variant=variant,
        scenario=scenario,
        task_id="task-x",
        final_status="awaiting_user" if escalated else "completed",
        rounds=rounds,
        decisions=[],
        elapsed_seconds=wall,
        artifacts_total_chars=0,
        escalated=escalated,
        should_escalate=should,
        completed_after_followup=completed_after,
        error=error,
    )


def test_aggregate_computes_per_variant_rates() -> None:
    rows = [
        _row("baseline", "u1", escalated=False, should=True),
        _row("baseline", "u2", escalated=False, should=True),
        _row("baseline", "c1", escalated=False, should=False),
        _row("planner_gate", "u1", escalated=True, should=True, completed_after=True),
        _row("planner_gate", "u2", escalated=True, should=True, completed_after=False),
        _row("planner_gate", "c1", escalated=False, should=False),
    ]
    agg = _aggregate(rows)

    assert agg["baseline"]["underspec_total"] == 2
    assert agg["baseline"]["underspec_escalated"] == 0
    assert agg["baseline"]["underspec_escalation_rate"] == 0.0
    assert agg["baseline"]["control_false_escalation_rate"] == 0.0

    assert agg["planner_gate"]["underspec_escalated"] == 2
    assert agg["planner_gate"]["underspec_escalation_rate"] == 1.0
    assert agg["planner_gate"]["completed_after_followup"] == 1
    assert agg["planner_gate"]["control_false_escalation_rate"] == 0.0


def test_aggregate_handles_zero_denominators() -> None:
    rows = [_row("only_under", "u1", escalated=True, should=True)]
    agg = _aggregate(rows)
    assert agg["only_under"]["control_total"] == 0
    assert agg["only_under"]["control_false_escalation_rate"] is None


def test_markdown_summary_renders_without_crashing() -> None:
    rows = [
        _row("baseline", "u1", escalated=False, should=True),
        _row("planner_gate", "u1", escalated=True, should=True, completed_after=True),
    ]
    md = _markdown_summary(rows, _aggregate(rows))
    assert "F2 escalation lab summary" in md
    assert "baseline" in md
    assert "planner_gate" in md
    # Per-run detail rows include the OK/NG marker.
    assert "✅" in md
    assert "❌" in md
