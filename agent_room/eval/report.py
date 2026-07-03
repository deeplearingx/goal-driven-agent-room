"""Aggregate run records into pass-rate / cost tables (markdown + json).

The per-variant pass rate + mean cost table is the north-star artifact: it is
what EVAL-2's ablation matrix renders and WRITE-1 quotes.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import asdict
from typing import Any

from agent_room.eval.runner import RunRecord


def aggregate(records: Sequence[RunRecord]) -> dict[str, dict[str, Any]]:
    """Per-variant rollup: pass rate, mean wall time, total tokens, errors."""

    by_variant: dict[str, dict[str, Any]] = {}
    for record in records:
        agg = by_variant.setdefault(
            record.variant,
            {
                "total": 0,
                "passed": 0,
                "errors": 0,
                "wall_total": 0.0,
                "tokens_total": 0,
                "tokens_seen": False,
            },
        )
        agg["total"] += 1
        if record.passed:
            agg["passed"] += 1
        if record.error:
            agg["errors"] += 1
        agg["wall_total"] += record.elapsed_seconds
        if record.total_tokens is not None:
            agg["tokens_total"] += record.total_tokens
            agg["tokens_seen"] = True

    for agg in by_variant.values():
        total = agg["total"]
        agg["pass_rate"] = agg["passed"] / total if total else None
        agg["mean_wall"] = agg["wall_total"] / total if total else None
        agg["tokens_total"] = agg["tokens_total"] if agg["tokens_seen"] else None
    return by_variant


def to_json(records: Sequence[RunRecord]) -> dict[str, Any]:
    return {
        "rows": [asdict(r) for r in records],
        "aggregate": aggregate(records),
    }


def _fmt_rate(passed: int, total: int, rate: float | None) -> str:
    if rate is None:
        return "n/a"
    return f"{passed}/{total} ({rate:.0%})"


def to_markdown(records: Sequence[RunRecord]) -> str:
    agg = aggregate(records)
    lines = [
        "# eval summary",
        "",
        "| variant | pass | mean wall (s) | tokens | errors |",
        "|---|---|---|---|---|",
    ]
    for name, row in agg.items():
        tokens = "n/a" if row["tokens_total"] is None else str(row["tokens_total"])
        mean_wall = "n/a" if row["mean_wall"] is None else f"{row['mean_wall']:.2f}"
        lines.append(
            f"| {name} | "
            f"{_fmt_rate(row['passed'], row['total'], row['pass_rate'])} | "
            f"{mean_wall} | {tokens} | {row['errors']} |"
        )
    lines += [
        "",
        "## per-run detail",
        "",
        "| variant | task | pass | status | rounds | wall (s) | err |",
        "|---|---|---|---|---|---|---|",
    ]
    for record in records:
        mark = "✅" if record.passed else "❌"
        lines.append(
            f"| {record.variant} | {record.task} | {mark} | "
            f"{record.final_status} | {record.rounds} | "
            f"{record.elapsed_seconds} | {record.error or '-'} |"
        )
    return "\n".join(lines)
