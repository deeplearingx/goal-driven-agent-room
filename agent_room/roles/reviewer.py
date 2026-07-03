"""Reviewer role: emits a strict ReviewerDecision via structured output."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage

from agent_room.config import RoleBindings
from agent_room.llm import LangChainTransport, Transport
from agent_room.prompt.developer import render_review_feedback_block
from agent_room.roles._message import format_extra_context
from agent_room.schemas import Artifact, Event, ReviewerDecision
from agent_room.state import TaskState

REVIEWER_SYSTEM = """You are the reviewer in a multi-role engineering team.
Decide one of:
  - approved: code meets the plan and is fit for delivery.
  - revision_required: fixable issues; list them so developer can address each.
  - need_user_decision: ambiguity beyond the team's authority (e.g. product call).
Confidence is a float in [0, 1]. Be strict but constructive.

If the human message includes a "User directives" section, those are answers
the user gave after a prior `need_user_decision`. Verify they are visibly
applied; if not, set decision=revision_required and call out the gap."""


def make_reviewer(
    bindings: RoleBindings,
    *,
    prompt_override: str | None = None,
    extra_context_keys: list[str] | None = None,
    model: str | None = None,
    transport: Transport | None = None,
) -> Callable[[TaskState], Awaitable[dict[str, Any]]]:
    tx: Transport = transport or LangChainTransport(bindings)
    system_prompt = prompt_override or REVIEWER_SYSTEM

    async def reviewer(state: TaskState) -> dict[str, Any]:
        round_no = state.get("revision_round", 0)
        directives = state.get("user_directives") or []

        sections = [
            f"# Title\n{state['title']}",
            f"\n# Description\n{state['description']}",
            f"\n# Plan\n{state.get('plan', '')}",
            f"\n# Code\n{state.get('code', '')}",
        ]
        if directives:
            joined = "\n".join(f"- {d}" for d in directives)
            sections.append(
                "\n# User directives (must be visibly applied; flag any gap)\n" + joined
            )

        extra = format_extra_context(state, extra_context_keys)
        if extra:
            sections.append("\n" + extra)

        sections.append(
            f"\nReview round: {round_no}\nMax revisions allowed: {state.get('max_revisions', 2)}"
        )

        msgs = [
            SystemMessage(content=system_prompt),
            HumanMessage(content="\n".join(sections)),
        ]
        decision: ReviewerDecision = await tx.structured(
            "reviewer", ReviewerDecision, msgs, model_override=model
        )
        update: dict[str, Any] = {
            "review": decision,
            "revision_round": round_no + 1,
            "artifacts": [
                Artifact(
                    kind="review",
                    role="reviewer",
                    content=decision.feedback,
                    round=round_no,
                    extra=decision.model_dump(),
                )
            ],
            "events": [
                Event(
                    type="reviewer_completed",
                    role="reviewer",
                    round=round_no,
                    payload={"decision": decision.decision},
                )
            ],
        }
        # Transcript-push: a ReAct developer's `dev_messages` is append-only
        # and its initial prompt (which carries the review_feedback layer)
        # never re-renders on re-entry — without this append, a sent-back
        # ReAct developer retried blind on its stale transcript, never seeing
        # the fresh feedback. Appending here (at the information's source)
        # fixes that for every ReAct preset; when `dev_messages` is empty
        # (non-ReAct presets) this is skipped and behavior is unchanged.
        # `dev_round: 0` gives the revision a fresh tool budget instead of
        # inheriting a possibly-exhausted one.
        if decision.decision == "revision_required" and state.get("dev_messages"):
            feedback_block = render_review_feedback_block(decision, round_no=round_no)
            update["dev_messages"] = [HumanMessage(content=feedback_block)]
            update["dev_round"] = 0
        return update

    return reviewer
