"""Eval variants for the coding suite.

A `CodingTask` only closes the loop if the developer actually *edits the
sandbox* (via `write_text`) and *runs the suite* (via `shell`) — otherwise the
oracle runs pytest against the unmodified buggy seed and always fails. So the
baseline coding variant must be a tool-enabled ReAct developer, mirroring
`examples/dev_runs_tests.py`. Tools are named here; their sandbox-pinned
instances come from `CodingTask.registry()` at graph-build time.

EVAL-2 layers ablation axes (planner_gate / two-call review / context / memory)
on top of this tool-enabled baseline — tools are a precondition for coding
tasks, not an ablation axis (no-tools is expected ~0% and lands as a control).
"""

from __future__ import annotations

from agent_room.config import RoleBindings, Settings, load_settings
from agent_room.context import SummaryContextEngine, WindowedContextEngine
from agent_room.eval import Variant
from agent_room.llm.context_window import compression_budget
from agent_room.memory import FileFtsMemoryProvider
from agent_room.spec import GraphSpec, load_preset


def tool_developer_spec() -> GraphSpec:
    """Full pipeline with a tool-using, unrestricted ReAct developer."""

    return GraphSpec.model_validate(
        {
            "name": "coding_tools",
            "entry": "planner",
            "nodes": {
                "planner": {"role": "planner"},
                "developer": {
                    "role": "developer",
                    "tools": ["glob", "read_text", "write_text", "shell"],
                    "tool_mode": "unrestricted",
                    "max_dev_rounds": 8,
                },
                "reviewer": {"role": "reviewer"},
                "delivery": {"role": "delivery"},
            },
            "edges": [
                {"from": "planner", "to": "developer"},
                {"from": "developer", "to": "reviewer"},
                {
                    "from": "reviewer",
                    "branches": [
                        {"on": "developer", "to": "developer"},
                        {"on": "delivery", "to": "delivery"},
                        {"on": "halt", "to": "__end__"},
                    ],
                },
                {"from": "delivery", "to": "__end__"},
            ],
        }
    )


def coding_baseline() -> Variant:
    return Variant(
        name="baseline_tools",
        spec=tool_developer_spec(),
        halt_node="reviewer",
        notes="Tool-enabled ReAct developer; the floor every coding ablation builds on.",
    )


def coding_windowed() -> Variant:
    """Same developer, but its ReAct history is bounded by a Windowed engine.

    Ablation partner for `coding_baseline` (NoOp): does compressing the
    developer's working memory preserve correctness on a multi-round task?
    `max_messages=8` with a 2-message head + 4-message tail engages once a task
    needs ~4+ tool rounds, dropping the middle.
    """

    def _bindings(settings: Settings | None) -> RoleBindings:
        return RoleBindings(
            settings=settings,
            context_engine=WindowedContextEngine(
                max_messages=8, protect_first_n=2, protect_last_n=4
            ),
        )

    return Variant(
        name="windowed_ctx",
        spec=tool_developer_spec(),
        bindings_factory=_bindings,
        halt_node="reviewer",
        notes="Developer compresses its ReAct history (WindowedContextEngine).",
    )


def coding_summary() -> Variant:
    """Same as `coding_windowed`, but the middle is *summarised* (hermes-style)
    instead of dropped.

    The Windowed engine drops the middle, destroying the developer's working
    memory and causing it to churn / re-explore. Summarising the dropped span
    (borrowed shape, ADR-0007) should keep enough memory to converge. Same
    window params, so the only variable is drop-vs-summarise.
    """

    def _bindings(settings: Settings | None) -> RoleBindings:
        summarizer = RoleBindings(settings=settings).resolve("delivery")
        return RoleBindings(
            settings=settings,
            context_engine=SummaryContextEngine(
                summarizer=summarizer,
                max_messages=8,
                protect_first_n=2,
                protect_last_n=4,
            ),
        )

    return Variant(
        name="summary_ctx",
        spec=tool_developer_spec(),
        bindings_factory=_bindings,
        halt_node="reviewer",
        notes="Developer summarises (not drops) compressed history (SummaryContextEngine).",
    )


def coding_summary_tokens(percent: float = 0.5) -> Variant:
    """Summary engine triggered by **token pressure** (tiktoken), not message
    count. The budget is `context_window × percent` (hermes shape) resolved from
    the configured model — so it compresses only when the history approaches a
    real fraction of the window, and small conversations pay no overhead."""

    def _bindings(settings: Settings | None) -> RoleBindings:
        resolved = settings or load_settings()
        budget = compression_budget(resolved.default_model, percent=percent)
        summarizer = RoleBindings(settings=settings).resolve("delivery")
        return RoleBindings(
            settings=settings,
            context_engine=SummaryContextEngine(
                summarizer=summarizer,
                max_tokens=budget,
                protect_first_n=2,
                target_ratio=0.5,
            ),
        )

    return Variant(
        name="summary_tokens_ctx",
        spec=tool_developer_spec(),
        bindings_factory=_bindings,
        halt_node="reviewer",
        notes="Summary engine, token-pressure trigger (max_tokens) instead of message count.",
    )


# --- memory ablation (does cross-session memory help recall a seeded fact?) ---


def coding_memory_noop() -> Variant:
    return Variant(
        name="memory_noop",
        spec=tool_developer_spec(),
        halt_node="reviewer",
        notes="Default NoOp memory — the developer can't recall the seeded marker.",
    )


def coding_memory_fts() -> Variant:
    """FileFts memory; the provider is initialized by `sqlite_graph_builder`
    (lifecycle in `build_with_sqlite_checkpointer`) reading the seeded
    `MEMORY.md`, so the developer sees the marker in its system prompt."""

    def _bindings(settings: Settings | None) -> RoleBindings:
        return RoleBindings(settings=settings, memory=FileFtsMemoryProvider())

    return Variant(
        name="memory_fts",
        spec=tool_developer_spec(),
        bindings_factory=_bindings,
        halt_node="reviewer",
        notes="FileFts memory — the developer recalls the seeded marker.",
    )


# --- mode A/B (workflow vs goal — the product's two operating modes) ---------
# Same request, same developer node config (full_react and goal declare the
# identical ReAct developer); the only axis is what sits after the developer:
# a subjective reviewer vs the objective verify loop.


def mode_workflow() -> Variant:
    return Variant(
        name="workflow",
        spec=load_preset("full_react"),
        halt_node="reviewer",
        notes="Workflow mode: single pass, reviewer's subjective approval is the exit.",
    )


def mode_goal() -> Variant:
    return Variant(
        name="goal",
        spec=load_preset("goal"),
        halt_node="reviewer",
        notes="Goal mode: verify_command oracle drives the iterate-until-green loop.",
    )


# --- planner_gate ablation (escalation suite) --------------------------------
# Both use env-driven default bindings. The structured-output nodes (planner
# gate + reviewer) work on thinking-mode models out of the box — the transport
# falls back to a JSON-prompt parse when the provider rejects forced tool_choice
# (F2 fix). Pinning them to a non-thinking model is now only an optimization.


def escalation_baseline() -> Variant:
    return Variant(
        name="baseline",
        spec=load_preset("full"),
        halt_node="reviewer",
        notes="Single-call reviewer; the F2 baseline that invents under-spec answers.",
    )


def escalation_planner_gate() -> Variant:
    return Variant(
        name="planner_gate",
        spec=load_preset("planner_gate"),
        halt_node="planner",
        notes="Planner emits open_questions; the gate halts before the developer.",
    )


def escalation_two_call() -> Variant:
    return Variant(
        name="two_call_review",
        spec=load_preset("two_call_review"),
        halt_node="reviewer",
        notes="Reviewer focus-checks 'is this fully specified?' first, then escalates.",
    )
