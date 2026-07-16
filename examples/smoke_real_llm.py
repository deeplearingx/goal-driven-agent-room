"""Real-LLM smoke harness for agent-room.

Drives the full graph against a live provider (defaults to whatever
`AGENT_ROOM_DEFAULT_MODEL` resolves to — typically Volcano Ark via
`ANTHROPIC_BASE_URL`/`ANTHROPIC_AUTH_TOKEN`).

Goals (per the v0.1 → v0.2 plan):
  1. Verify `with_structured_output(ReviewerDecision)` actually works on the
     chosen provider — no fake parser, no JSON salvage.
  2. Verify SSE-level streaming round-trips real `astream_events` output.
  3. Collect concrete timing / size / decision data so we can size
     [PLAN.md] RISK-9 spill caps with numbers, not guesses.
  4. Surface prompt-engineering gaps before v0.2 GraphSpec design.

Run:
    cp .env.example .env  # then fill in real creds
    AGENT_ROOM_DB=./snapshots/smoke.db \\
        python -m examples.smoke_real_llm

Reads creds from the process env (or `.env` next to the project root).
Skips quietly when no provider is configured, so this stays safe to invoke
from CI or hooks.

Outputs JSON snapshots to ./snapshots/smoke-<task_id>.json — gitignored.
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
from agent_room.schemas import TaskRequest
from agent_room.service import AgentRoomService

SNAPSHOT_DIR = Path(__file__).resolve().parent.parent / "snapshots"


@dataclass
class Scenario:
    """A single smoke scenario."""

    name: str
    task: TaskRequest
    follow_ups: list[str] = field(default_factory=list)
    """Each entry is `service.resume()`'d in turn after a pause."""


@dataclass
class RunResult:
    """Captured per-scenario telemetry — the whole point of this script."""

    name: str
    task_id: str
    final_status: str
    rounds: int
    decisions: list[str]
    elapsed_seconds: float
    artifacts_count: int
    artifacts_total_chars: int
    events_count: int
    user_directives: list[str]
    delivery_chars: int | None
    plan_chars: int | None
    code_chars: int | None
    error: str | None = None


SCENARIOS: list[Scenario] = [
    Scenario(
        name="happy_path",
        task=TaskRequest(
            title="Sum-of-two function",
            description=(
                "Implement `add(a: int, b: int) -> int` in Python. "
                "Include a docstring and a couple of assertions as a smoke test."
            ),
            max_revisions=2,
        ),
    ),
    Scenario(
        name="revision_loop",
        task=TaskRequest(
            title="LRU cache class",
            description=(
                "Implement a Python class `LRUCache(capacity: int)` with `get(key)` "
                "and `put(key, value)` methods, both O(1). Use only the standard library. "
                "Include at least one usage example with assertions."
            ),
            max_revisions=3,
        ),
    ),
    Scenario(
        name="need_user_decision",
        task=TaskRequest(
            title="HTTP retry helper",
            description=(
                "Build a small Python helper for HTTP retry-with-backoff. "
                "Pick a sensible default behaviour. If you need a product call "
                "(e.g. should we retry on 4xx or only 5xx, do we add jitter), "
                "ASK with reviewer.decision='need_user_decision' rather than "
                "guessing — the team is here to answer."
            ),
            max_revisions=2,
        ),
        follow_ups=[
            "Retry only on 5xx and 429. Use exponential backoff with full jitter. "
            "Cap at 5 attempts. Do not retry on 4xx other than 429."
        ],
    ),
    Scenario(
        # F2 follow-up scenario, 2026-06-11. Deliberately *does not* tell the
        # reviewer "ask if unsure" — the new REVIEWER_SYSTEM prompt should
        # catch real under-specification without hand-holding from the task
        # description. If reviewer still picks `approved` here, F2 prompt
        # rewrite didn't land.
        name="under_specified_rate_limiter",
        task=TaskRequest(
            title="Rate limiter for our API client",
            description=(
                "Add a rate limiter to our outbound HTTP client. Make it production-grade."
            ),
            max_revisions=2,
        ),
        follow_ups=[
            "Token bucket, 50 req/sec sustained, burst 100. "
            "Block (sleep) when budget exhausted, do not raise. "
            "Per-process, in-memory state is fine for now."
        ],
    ),
]


async def run_scenario(service: AgentRoomService, scenario: Scenario) -> RunResult:
    task_id = service.new_task_id()
    start = time.perf_counter()
    decisions: list[str] = []
    error: str | None = None

    try:
        snap = await service.run(scenario.task, task_id=task_id)
        decisions.append(_decision_of(snap))

        for follow_up in scenario.follow_ups:
            if snap.status != "awaiting_user":
                # Provider didn't park the run for input — record and stop.
                break
            snap = await service.resume(task_id, follow_up)
            decisions.append(_decision_of(snap))
    except Exception as exc:  # noqa: BLE001 — smoke harness records, doesn't raise
        error = f"{type(exc).__name__}: {exc}"
        # Best-effort recover whatever did land in checkpointer.
        full = await service.snapshot(task_id)
        snap = full

    elapsed = time.perf_counter() - start
    state = await service.graph.aget_state({"configurable": {"thread_id": task_id}})
    values = state.values or {}

    artifacts = values.get("artifacts") or []
    events = values.get("events") or []
    plan = values.get("plan") or ""
    code = values.get("code") or ""
    delivery = values.get("delivery")

    return RunResult(
        name=scenario.name,
        task_id=task_id,
        final_status=snap.status,
        rounds=values.get("revision_round", 0),
        decisions=decisions,
        elapsed_seconds=round(elapsed, 2),
        artifacts_count=len(artifacts),
        artifacts_total_chars=sum(len(getattr(a, "content", "") or "") for a in artifacts),
        events_count=len(events),
        user_directives=values.get("user_directives") or [],
        delivery_chars=len(delivery) if isinstance(delivery, str) else None,
        plan_chars=len(plan),
        code_chars=len(code),
        error=error,
    )


def _decision_of(snap: Any) -> str:
    review = getattr(snap, "review", None)
    if review is None:
        return "none"
    return getattr(review, "decision", "unknown")


def _ensure_provider_configured() -> bool:
    settings = load_settings()
    if settings.anthropic_auth_token or settings.anthropic_api_key or settings.openai_api_key:
        return True
    print("No provider configured — copy .env.example → .env and fill creds. Skipping.")
    return False


def _print_summary(results: list[RunResult]) -> None:
    print("\n" + "=" * 72)
    print("SMOKE SUMMARY")
    print("=" * 72)
    for r in results:
        marker = "FAIL" if r.error else r.final_status.upper()
        print(
            f"[{marker:>14}] {r.name:<22} "
            f"rounds={r.rounds} decisions={r.decisions} "
            f"{r.elapsed_seconds:.1f}s "
            f"artifacts={r.artifacts_count}/{r.artifacts_total_chars}c "
            f"events={r.events_count}"
        )
        if r.error:
            print(f"   error: {r.error}")
    print()


async def main() -> int:
    # Force .env to win over shell — the smoke harness's whole point is to
    # exercise *this project's* configured creds, not whatever proxy the
    # surrounding shell happens to point at (Claude Code, etc.).
    try:
        from dotenv import load_dotenv

        load_dotenv(override=True)
    except ImportError:
        pass

    if not _ensure_provider_configured():
        return 0

    SNAPSHOT_DIR.mkdir(exist_ok=True)
    db_path = os.getenv("AGENT_ROOM_DB", str(SNAPSHOT_DIR / "smoke.db"))

    settings = load_settings()
    print(f"Provider: model={settings.default_model!r} base_url={settings.anthropic_base_url!r}")
    token = settings.anthropic_auth_token or settings.anthropic_api_key or ""
    print(f"Auth: {'***' + token[-6:] if token else '(none)'}")
    print(f"DB: {db_path}\n")

    only = os.getenv("AGENT_ROOM_SMOKE_ONLY")
    scenarios = [s for s in SCENARIOS if not only or s.name == only]
    if not scenarios:
        print(f"No scenario matched AGENT_ROOM_SMOKE_ONLY={only!r}")
        return 1

    bindings = RoleBindings(settings=settings)
    results: list[RunResult] = []

    async with build_with_sqlite_checkpointer(bindings, db_path=db_path) as graph:
        service = AgentRoomService(graph)
        for scenario in scenarios:
            print(f"--- Running {scenario.name} ---")
            result = await run_scenario(service, scenario)
            results.append(result)
            out = SNAPSHOT_DIR / f"smoke-{scenario.name}-{result.task_id}.json"
            out.write_text(json.dumps(asdict(result), indent=2, ensure_ascii=False))
            print(f"  → {out.relative_to(SNAPSHOT_DIR.parent)}")

    _print_summary(results)
    summary_path = SNAPSHOT_DIR / "smoke-summary.json"
    summary_path.write_text(json.dumps([asdict(r) for r in results], indent=2, ensure_ascii=False))
    print(f"Summary: {summary_path.relative_to(SNAPSHOT_DIR.parent)}")
    return 0 if not any(r.error for r in results) else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
