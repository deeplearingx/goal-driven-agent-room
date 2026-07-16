"""Lab runner — drives every (variant, scenario) pair, records telemetry.

Output:
  snapshots/escalation/{variant}-{scenario}-{task_id}.json   per-run detail
  snapshots/escalation/summary.json                          full matrix
  snapshots/escalation/summary.md                            human-readable

The runner is intentionally *single-process and serial*; lab calls touch
a real provider, and the goal is reproducible measurement, not raw speed.
Skips silently when no provider creds are present (so CI / hooks don't
explode).
"""

from __future__ import annotations

import asyncio
import json
import os
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from agent_room.config import RoleBindings, load_settings
from agent_room.graph import build_with_sqlite_checkpointer
from agent_room.service import AgentRoomService
from examples.escalation_lab.scenarios import LabScenario, filter_scenarios
from examples.escalation_lab.variants import LabVariant, build_variants, filter_variants

OUT_DIR = Path(__file__).resolve().parent.parent.parent / "snapshots" / "escalation"


@dataclass
class RunRow:
    variant: str
    scenario: str
    task_id: str
    final_status: str
    rounds: int
    decisions: list[str]
    elapsed_seconds: float
    artifacts_total_chars: int
    escalated: bool
    should_escalate: bool
    completed_after_followup: bool | None
    error: str | None = None
    open_questions_seen: list[str] = field(default_factory=list)


def _decision_of(snap: Any) -> str:
    review = getattr(snap, "review", None)
    if review is None:
        return "none"
    return getattr(review, "decision", "unknown")


async def _run_one(
    service: AgentRoomService,
    variant: LabVariant,
    scenario: LabScenario,
    *,
    timeout_s: float,
) -> RunRow:
    task_id = service.new_task_id()
    start = time.perf_counter()
    decisions: list[str] = []
    error: str | None = None
    completed_after_followup: bool | None = None
    open_questions_seen: list[str] = []

    try:
        snap = await asyncio.wait_for(
            service.run(scenario.task, task_id=task_id), timeout=timeout_s
        )
        decisions.append(_decision_of(snap))

        escalated = snap.status == "awaiting_user"

        # Capture state-level open_questions if the variant set them.
        state = await service.graph.aget_state({"configurable": {"thread_id": task_id}})
        open_questions_seen = list(state.values.get("open_questions") or [])

        if escalated and scenario.follow_up:
            snap = await asyncio.wait_for(
                service.resume(task_id, scenario.follow_up, at_node=variant.halt_node),
                timeout=timeout_s,
            )
            decisions.append(_decision_of(snap))
            completed_after_followup = snap.status == "completed"
    except TimeoutError:
        error = f"timeout after {timeout_s}s"
        snap = await service.snapshot(task_id)
        escalated = snap.status == "awaiting_user"
    except Exception as exc:  # noqa: BLE001 — lab records, doesn't raise
        error = f"{type(exc).__name__}: {exc}"
        snap = await service.snapshot(task_id)
        escalated = snap.status == "awaiting_user"

    elapsed = time.perf_counter() - start
    state = await service.graph.aget_state({"configurable": {"thread_id": task_id}})
    values = state.values or {}
    artifacts = values.get("artifacts") or []

    return RunRow(
        variant=variant.name,
        scenario=scenario.name,
        task_id=task_id,
        final_status=snap.status,
        rounds=values.get("revision_round", 0),
        decisions=decisions,
        elapsed_seconds=round(elapsed, 2),
        artifacts_total_chars=sum(len(getattr(a, "content", "") or "") for a in artifacts),
        escalated=escalated,
        should_escalate=scenario.should_escalate,
        completed_after_followup=completed_after_followup,
        error=error,
        open_questions_seen=open_questions_seen,
    )


def _aggregate(rows: list[RunRow]) -> dict[str, Any]:
    """Per-variant escalation rate on under-specified scenarios + control rate."""

    by_variant: dict[str, dict[str, Any]] = {}
    for row in rows:
        v = by_variant.setdefault(
            row.variant,
            {
                "underspec_total": 0,
                "underspec_escalated": 0,
                "control_total": 0,
                "control_escalated": 0,
                "wall_time_total": 0.0,
                "errors": 0,
                "completed_after_followup": 0,
            },
        )
        if row.error:
            v["errors"] += 1
        v["wall_time_total"] += row.elapsed_seconds
        if row.should_escalate:
            v["underspec_total"] += 1
            if row.escalated:
                v["underspec_escalated"] += 1
                if row.completed_after_followup:
                    v["completed_after_followup"] += 1
        else:
            v["control_total"] += 1
            if row.escalated:
                v["control_escalated"] += 1

    for v in by_variant.values():
        u = v["underspec_total"]
        v["underspec_escalation_rate"] = v["underspec_escalated"] / u if u else None
        c = v["control_total"]
        v["control_false_escalation_rate"] = v["control_escalated"] / c if c else None
    return by_variant


def _markdown_summary(rows: list[RunRow], aggregate: dict[str, Any]) -> str:
    def fmt_rate(num: int, denom: int, rate: float | None) -> str:
        if rate is None:
            return "n/a"
        return f"{num}/{denom} ({rate:.0%})"

    lines = [
        "# F2 escalation lab summary",
        "",
        "| variant | under-spec escalation | control false-escalation | wall (s) | errors | resumed→completed |",
        "|---|---|---|---|---|---|",
    ]
    for variant_name, agg in aggregate.items():
        lines.append(
            f"| {variant_name} | "
            f"{fmt_rate(agg['underspec_escalated'], agg['underspec_total'], agg['underspec_escalation_rate'])} | "
            f"{fmt_rate(agg['control_escalated'], agg['control_total'], agg['control_false_escalation_rate'])} | "
            f"{agg['wall_time_total']:.1f} | "
            f"{agg['errors']} | "
            f"{agg['completed_after_followup']} |"
        )
    lines.append("")
    lines.append("## per-run detail")
    lines.append("")
    lines.append("| variant | scenario | escalated? | should? | rounds | wall (s) | err |")
    lines.append("|---|---|---|---|---|---|---|")
    for row in rows:
        ok = "✅" if row.escalated == row.should_escalate else "❌"
        lines.append(
            f"| {row.variant} | {row.scenario} | "
            f"{'yes' if row.escalated else 'no'} | "
            f"{'yes' if row.should_escalate else 'no'} {ok} | "
            f"{row.rounds} | {row.elapsed_seconds} | "
            f"{row.error or '-'} |"
        )
    return "\n".join(lines)


async def main() -> int:
    try:
        from dotenv import load_dotenv

        load_dotenv(override=True)
    except ImportError:
        pass

    settings = load_settings()
    if not (settings.anthropic_auth_token or settings.anthropic_api_key or settings.openai_api_key):
        print("No provider configured — skipping escalation lab.")
        return 0

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    db_path = os.getenv("AGENT_ROOM_LAB_DB") or str(OUT_DIR / "lab.db")

    print(f"Provider: model={settings.default_model!r} base_url={settings.anthropic_base_url!r}")
    print(f"DB: {db_path}\n")

    variant_filter = (os.getenv("AGENT_ROOM_LAB_VARIANTS") or "").split(",")
    variant_filter = [v for v in variant_filter if v]
    scenario_filter = (os.getenv("AGENT_ROOM_LAB_SCENARIOS") or "").split(",")
    scenario_filter = [s for s in scenario_filter if s]

    variants = filter_variants(build_variants(), variant_filter or None)
    scenarios = filter_scenarios(scenario_filter or None)
    if not variants or not scenarios:
        print(f"Empty filter (variants={variant_filter}, scenarios={scenario_filter}). Stopping.")
        return 1

    bindings = RoleBindings(settings=settings)
    rows: list[RunRow] = []

    timeout_s = float(os.getenv("AGENT_ROOM_LAB_TIMEOUT") or "600")
    print(f"Per-run timeout: {timeout_s}s\n")

    for variant in variants:
        print(f"=== variant: {variant.name} ===")
        async with build_with_sqlite_checkpointer(
            bindings, db_path=db_path, spec=variant.spec
        ) as graph:
            service = AgentRoomService(graph)
            for scenario in scenarios:
                print(f"  --- {scenario.name} ---")
                row = await _run_one(service, variant, scenario, timeout_s=timeout_s)
                rows.append(row)
                out_file = OUT_DIR / f"{variant.name}-{scenario.name}-{row.task_id}.json"
                out_file.write_text(json.dumps(asdict(row), indent=2, ensure_ascii=False))
                marker = "OK" if row.escalated == row.should_escalate else "MISMATCH"
                print(
                    f"    [{marker}] status={row.final_status} rounds={row.rounds} "
                    f"escalated={row.escalated} wall={row.elapsed_seconds}s"
                )

    aggregate = _aggregate(rows)
    summary_path = OUT_DIR / "summary.json"
    summary_path.write_text(
        json.dumps(
            {"rows": [asdict(r) for r in rows], "aggregate": aggregate},
            indent=2,
            ensure_ascii=False,
        )
    )
    md_path = OUT_DIR / "summary.md"
    md_path.write_text(_markdown_summary(rows, aggregate))
    print(f"\nSummary: {summary_path}")
    print(f"Markdown: {md_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
