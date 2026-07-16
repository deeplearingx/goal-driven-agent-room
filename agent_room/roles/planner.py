"""Planner role: turns the request into a numbered plan."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage

from agent_room.config import RoleBindings
from agent_room.llm import LangChainTransport, Transport
from agent_room.roles._message import extract_text, format_extra_context
from agent_room.schemas import Artifact, Event
from agent_room.state import TaskState

PLANNER_SYSTEM = """You are the planner in a multi-role engineering team.
Produce a concise, numbered implementation plan. Avoid code in this step;
focus on steps, contracts, and sequencing. Keep it under 12 bullets."""


def make_planner(
    bindings: RoleBindings,
    *,
    prompt_override: str | None = None,
    extra_context_keys: list[str] | None = None,
    model: str | None = None,
    transport: Transport | None = None,
) -> Callable[[TaskState], Awaitable[dict[str, Any]]]:
    tx: Transport = transport or LangChainTransport(bindings)
    system_prompt = prompt_override or PLANNER_SYSTEM

    async def planner(state: TaskState) -> dict[str, Any]:
        human_parts = [f"Title: {state['title']}\n\nDescription:\n{state['description']}"]
        extra = format_extra_context(state, extra_context_keys)
        if extra:
            human_parts.append(extra)
        # Re-plan runs (goal mode: supervisor decided "replan" mid-loop) get
        # the failure context so the new plan addresses what's actually
        # blocking, not a fresh restatement of the original request.
        verification = state.get("verification")
        prior_plan = state.get("plan")
        if verification is not None and not verification.passed and prior_plan:
            human_parts.append(
                f"# Previous plan (did not reach the goal)\n{prior_plan}\n\n"
                f"# Latest verification failure (round {verification.round})\n"
                f"{verification.output_tail}\n\n"
                "Produce a REVISED plan that takes a different approach to the "
                "part that keeps failing."
            )
        msgs = [
            SystemMessage(content=system_prompt),
            HumanMessage(content="\n\n".join(human_parts)),
        ]
        response = await tx.invoke("planner", msgs, model_override=model)
        plan = extract_text(response.message)
        update: dict[str, Any] = {
            "plan": plan,
            "artifacts": [Artifact(kind="plan", role="planner", content=plan, round=0)],
            "events": [Event(type="planner_completed", role="planner")],
        }
        # Transcript-push (same pattern as reviewer.py): an in-flight ReAct
        # developer never re-renders its initial prompt, so a revised plan
        # must be appended to `dev_messages` to be seen. Empty transcript
        # (every existing preset — planner always runs before developer) ⇒
        # skipped, zero behavior change outside goal-mode re-plans.
        if state.get("dev_messages"):
            update["dev_messages"] = [HumanMessage(content=f"# Revised plan\n{plan}")]
            update["dev_round"] = 0
        return update

    return planner
