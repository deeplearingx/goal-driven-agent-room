"""Planner-gate variant — F2 escalation experiment, option (2).

The default planner emits free-form text and lets the developer figure out
what's missing. The gate variant asks the planner to commit to a structured
output: a numbered plan PLUS an explicit `open_questions` list. If the list
is non-empty, the gate node short-circuits the run by writing a synthetic
`need_user_decision` review into state, parking the run at `awaiting_user`
before any code is written.

The motivation comes from [docs/findings/2026-06-11-smoke-v0.1.md] §F2:
LLMs trained for code review are biased to "find issues" not "escalate
ambiguity," so retrofitting escalation onto the reviewer is fighting the
model's training. The planner has not been trained on adversarial review
data — flipping the framing to "what would you ask before starting?" is a
more natural prompt context for surfacing ambiguity.

Selected via spec: `nodes.planner = {role: planner, variant: gate}`.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage

from agent_room.config import RoleBindings
from agent_room.llm import LangChainTransport, Transport
from agent_room.roles._message import format_extra_context
from agent_room.schemas import Artifact, Event, PlannerGateOutput, ReviewerDecision
from agent_room.state import TaskState

PLANNER_GATE_SYSTEM = """You are the planner in a multi-role engineering team.

Two outputs, both required:

1) `plan` — a concise, numbered implementation plan (under 12 bullets).
   Avoid code; focus on steps, contracts, and sequencing.

2) `open_questions` — a list of questions a human MUST answer before this
   plan can be safely executed. Treat unstated product decisions, missing
   non-functional requirements (rate limits, error policies, persistence
   semantics, etc.), and ambiguous interfaces as open questions. Empty
   list ONLY when the task is genuinely fully specified.

Be honest. A short plan with three sharp questions beats a long plan that
silently invented answers. The team will pause and route the questions to
a human; nothing is wasted by surfacing ambiguity here."""


def make_planner_gate(
    bindings: RoleBindings,
    *,
    prompt_override: str | None = None,
    extra_context_keys: list[str] | None = None,
    model: str | None = None,
    transport: Transport | None = None,
) -> Callable[[TaskState], Awaitable[dict[str, Any]]]:
    tx: Transport = transport or LangChainTransport(bindings)
    system_prompt = prompt_override or PLANNER_GATE_SYSTEM

    async def planner_gate(state: TaskState) -> dict[str, Any]:
        human_parts = [f"Title: {state['title']}\n\nDescription:\n{state['description']}"]
        directives = state.get("user_directives") or []
        if directives:
            joined = "\n".join(f"- {d}" for d in directives)
            human_parts.append(f"# User directives (already provided)\n{joined}")
        extra = format_extra_context(state, extra_context_keys)
        if extra:
            human_parts.append(extra)

        msgs = [
            SystemMessage(content=system_prompt),
            HumanMessage(content="\n\n".join(human_parts)),
        ]
        result: PlannerGateOutput = await tx.structured(
            "planner", PlannerGateOutput, msgs, model_override=model
        )

        update: dict[str, Any] = {
            "plan": result.plan,
            "open_questions": result.open_questions,
            "artifacts": [
                Artifact(
                    kind="plan",
                    role="planner",
                    content=result.plan,
                    round=0,
                    extra=result.model_dump(),
                )
            ],
            "events": [
                Event(
                    type="planner_completed",
                    role="planner",
                    payload={"open_questions": len(result.open_questions)},
                )
            ],
        }

        if result.open_questions:
            # Park the run at `awaiting_user` by reusing the reviewer's
            # `need_user_decision` signal — `service.resume` already knows
            # how to recover from it. The questions are surfaced through
            # `feedback` so a human reading `result.review.feedback` sees
            # exactly what was unclear.
            joined = "\n".join(f"- {q}" for q in result.open_questions)
            update["review"] = ReviewerDecision(
                decision="need_user_decision",
                feedback=f"Planner gate flagged open questions:\n{joined}",
                issues=list(result.open_questions),
                confidence=1.0,
            )
            update["events"].append(
                Event(
                    type="planner_gate_escalated",
                    role="planner",
                    payload={"questions": list(result.open_questions)},
                )
            )

        return update

    return planner_gate
