"""Eval runner entrypoint — drive a suite against a real provider.

    python -m evals.run                          # default: coding suite
    AGENT_ROOM_EVAL_SUITE=planner_gate python -m evals.run

Skips quietly when no provider credentials are present (safe for CI / hooks).
Writes a per-run JSON + a markdown summary table to ./snapshots/eval/<suite>/.

Suites:
    coding        — CodingTask correctness (sandbox + pytest, hidden tests)
    planner_gate  — BehavioralTask escalation ablation: baseline vs planner_gate

Filters / knobs (optional env):
    AGENT_ROOM_EVAL_TASKS=underspec_cache,control_add_two_ints
    AGENT_ROOM_EVAL_TIMEOUT=300

Provider note (thinking models): structured-output nodes (reviewer, planner
gate) now work out of the box on thinking-mode models — the transport
(`agent_room/llm/transport.py`) falls back to a JSON-prompt parse when the
provider rejects the forced `tool_choice`. No env override is required.
Optionally pin those nodes to a non-thinking model to skip the fallback's extra
round-trip:

    AGENT_ROOM_REVIEWER_MODEL=deepseek-chat python -m evals.run        # coding
    AGENT_ROOM_EVAL_SUITE=planner_gate \\
      AGENT_ROOM_PLANNER_MODEL=deepseek-chat \\
      AGENT_ROOM_REVIEWER_MODEL=deepseek-chat python -m evals.run      # planner_gate
"""

from __future__ import annotations

import asyncio
import json
import os
from collections.abc import Sequence
from pathlib import Path

from agent_room.config import load_settings
from agent_room.eval import (
    Task,
    Variant,
    memory_graph_builder,
    run_matrix,
    sqlite_graph_builder,
    to_json,
    to_markdown,
)
from agent_room.eval.runner import RunRecord
from evals import CODING_TASKS
from evals.escalation_tasks import ESCALATION_TASKS
from evals.memory_tasks import MEMORY_EVAL_DB, MEMORY_TASKS
from evals.mode_tasks import MODE_TASKS
from evals.variants import (
    coding_baseline,
    coding_memory_fts,
    coding_memory_noop,
    coding_summary,
    coding_summary_tokens,
    coding_windowed,
    escalation_baseline,
    escalation_planner_gate,
    escalation_two_call,
    mode_goal,
    mode_workflow,
)

ROOT = Path(__file__).resolve().parent.parent


def _suite(name: str) -> tuple[list[Variant], list[Task]]:
    if name == "coding":
        return [coding_baseline()], list(CODING_TASKS)
    if name == "planner_gate":
        # 3-way escalation strategy: single reviewer vs planner-gate vs two-call review.
        return (
            [escalation_baseline(), escalation_planner_gate(), escalation_two_call()],
            list(ESCALATION_TASKS),
        )
    if name == "context":
        # 3-way on a multi-round task: NoOp vs Windowed (drop middle) vs Summary
        # (summarise middle). Does summarising preserve working memory where
        # dropping causes churn?
        task = next(t for t in CODING_TASKS if t.name == "fix_pipeline_multifile")
        # percent=0.05 is a *demo* budget: this small task (~20K tokens) wouldn't
        # reach a realistic 0.5 × window threshold, so it would never compress
        # (correct, but shows nothing). 0.05 forces engagement to exercise the
        # token-trigger path; production uses the 0.5 default.
        return (
            [
                coding_baseline(),
                coding_windowed(),
                coding_summary(),
                coding_summary_tokens(percent=0.05),
            ],
            [task],
        )
    if name == "memory":
        # NoOp vs FileFts on a task whose answer lives ONLY in seeded memory.
        return [coding_memory_noop(), coding_memory_fts()], list(MEMORY_TASKS)
    if name == "modes":
        # Workflow vs goal on underspecified-convention traps: does the
        # objective verify loop recover what subjective review waves through?
        return [mode_workflow(), mode_goal()], list(MODE_TASKS)
    raise SystemExit(
        f"unknown suite {name!r} (expected: coding | planner_gate | context | memory | modes)"
    )


def _have_credentials() -> bool:
    s = load_settings()
    return bool(s.anthropic_auth_token or s.anthropic_api_key or s.openai_api_key)


def _print_summary(records: Sequence[RunRecord]) -> None:
    passed = sum(1 for r in records if r.passed)
    print(f"\n=== {passed}/{len(records)} passed ===")
    for r in records:
        mark = "PASS" if r.passed else "FAIL"
        toks = "-" if r.total_tokens is None else str(r.total_tokens)
        print(
            f"  [{mark}] {r.variant}/{r.task}  status={r.final_status} "
            f"rounds={r.rounds} esc={r.escalated} wall={r.elapsed_seconds}s tokens={toks}"
        )


async def main() -> int:
    try:
        from dotenv import load_dotenv

        load_dotenv(override=True)
    except ImportError:
        pass

    if not _have_credentials():
        print(
            "No provider credentials in env "
            "(ANTHROPIC_AUTH_TOKEN / ANTHROPIC_API_KEY / OPENAI_API_KEY). "
            "The eval needs a real LLM — skipping. See .env.example."
        )
        return 0

    settings = load_settings()
    suite_name = os.getenv("AGENT_ROOM_EVAL_SUITE", "coding")
    timeout_s = float(os.getenv("AGENT_ROOM_EVAL_TIMEOUT") or "300")

    variants, all_tasks = _suite(suite_name)
    variant_filter = [v for v in (os.getenv("AGENT_ROOM_EVAL_VARIANTS") or "").split(",") if v]
    variants = [v for v in variants if not variant_filter or v.name in variant_filter]
    task_filter = [t for t in (os.getenv("AGENT_ROOM_EVAL_TASKS") or "").split(",") if t]
    tasks = [t for t in all_tasks if not task_filter or t.name in task_filter]

    print(f"Suite:    {suite_name}")
    print(f"Provider: model={settings.default_model!r} base_url={settings.anthropic_base_url!r}")
    print(f"Variants: {[v.name for v in variants]}")
    print(f"Tasks:    {[t.name for t in tasks]}")
    print(f"Per-run timeout: {timeout_s}s\n")

    # The memory suite needs the SQLite builder: that's where the memory
    # provider's lifecycle (initialize/close) is owned, so FileFts actually
    # reads the seeded MEMORY.md. Other suites use the default in-memory builder.
    graph_builder = (
        sqlite_graph_builder(MEMORY_EVAL_DB) if suite_name == "memory" else memory_graph_builder
    )
    records = await run_matrix(
        variants, tasks, settings=settings, graph_builder=graph_builder, timeout_s=timeout_s
    )

    out_dir = ROOT / "snapshots" / "eval" / suite_name
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "summary.json").write_text(
        json.dumps(to_json(records), indent=2, ensure_ascii=False), encoding="utf-8"
    )
    (out_dir / "summary.md").write_text(to_markdown(records), encoding="utf-8")

    _print_summary(records)
    print(f"\nMarkdown: {(out_dir / 'summary.md').relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
