"""Two-call reviewer variant — F2 escalation experiment, option (1).

The default reviewer makes a single structured `ReviewerDecision` call,
which the v0.1 smoke run showed is biased toward `revision_required` — the
model finds nits rather than escalating ambiguity (see findings §F2
attempt-2). The two-call variant decouples the two questions:

  1) `FocusCheck`: is the task fully specified? List open questions if not.
  2) `ReviewerDecision`: only when (1) is fully specified, ask "is the
     code right?" — the original framing the model is good at.

Empirically (to be measured by `examples/escalation_lab/`), step (1)'s
"is this fully specified?" framing dodges the find-issues bias because
the prompt doesn't ask about issues at all. When (1) reports
under-specification, the reviewer emits `need_user_decision` directly
and the second call is skipped — which also halves token spend on
under-specified runs.

Cost note: on fully-specified tasks (the common path for downstream
revision rounds), the two-call variant adds one cheap structured call.
The `FocusCheck` schema is small (~3 short fields) so the extra call is
typically <500 tokens.

Selected via spec: `nodes.reviewer = {role: reviewer, variant: two_call}`.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage

from agent_room.config import RoleBindings
from agent_room.llm import LangChainTransport, Transport
from agent_room.roles._message import format_extra_context
from agent_room.schemas import Artifact, Event, FocusCheck, ReviewerDecision
from agent_room.state import TaskState

FOCUS_CHECK_SYSTEM = """You are screening a task for a code-review team.

Answer one question only: is this task fully specified enough to be reviewed?

It IS fully specified when:
  - the contract / behaviour is unambiguous,
  - non-functional requirements (limits, error handling, persistence) are
    either stated or have a defensible default in the task domain,
  - any product decisions (which library, which protocol, which user UX)
    have already been made.

It is NOT fully specified when a reasonable engineer would have to invent
answers to make progress. List those answers as `open_questions`.

Do not review the code. Do not nitpick style. Only judge specification
completeness. An empty `open_questions` list means "yes, fully specified;
proceed to normal review.\""""

REVIEWER_TWO_CALL_SYSTEM = """You are the reviewer in a multi-role engineering team.
The task has already been screened as fully specified. Decide one of:
  - approved: code meets the plan and is fit for delivery.
  - revision_required: fixable issues; list them so developer can address each.
Confidence is a float in [0, 1]. Be strict but constructive.

Do NOT pick `need_user_decision` — that escalation path was already
checked upstream by the focus-check screen, and a re-escalation here is
almost always a `revision_required` in disguise."""


def make_reviewer_two_call(
    bindings: RoleBindings,
    *,
    prompt_override: str | None = None,
    extra_context_keys: list[str] | None = None,
    model: str | None = None,
    transport: Transport | None = None,
) -> Callable[[TaskState], Awaitable[dict[str, Any]]]:
    tx: Transport = transport or LangChainTransport(bindings)
    review_prompt = prompt_override or REVIEWER_TWO_CALL_SYSTEM

    async def reviewer_two_call(state: TaskState) -> dict[str, Any]:
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
        human_content = "\n".join(sections)

        # Call 1 — focus check. Cheap; reverses the bias frame.
        focus_msgs = [
            SystemMessage(content=FOCUS_CHECK_SYSTEM),
            HumanMessage(content=human_content),
        ]
        check: FocusCheck = await tx.structured(
            "reviewer", FocusCheck, focus_msgs, model_override=model
        )

        # Under-specified → escalate directly, skip the second call.
        if not check.is_fully_specified and check.open_questions and not directives:
            joined = "\n".join(f"- {q}" for q in check.open_questions)
            decision = ReviewerDecision(
                decision="need_user_decision",
                feedback=f"Focus check flagged open questions:\n{joined}",
                issues=list(check.open_questions),
                confidence=max(0.7, min(1.0, 1.0 - 0.05 * len(check.open_questions))),
            )
            return {
                "review": decision,
                "revision_round": round_no + 1,
                "open_questions": check.open_questions,
                "artifacts": [
                    Artifact(
                        kind="review",
                        role="reviewer",
                        content=decision.feedback,
                        round=round_no,
                        extra={"focus_check": check.model_dump(), **decision.model_dump()},
                    )
                ],
                "events": [
                    Event(
                        type="reviewer_focus_check_escalated",
                        role="reviewer",
                        round=round_no,
                        payload={"questions": list(check.open_questions)},
                    )
                ],
            }

        # Fully specified (or directives already provided) → normal review.
        review_msgs = [
            SystemMessage(content=review_prompt),
            HumanMessage(content=human_content),
        ]
        decision = await tx.structured(
            "reviewer", ReviewerDecision, review_msgs, model_override=model
        )
        return {
            "review": decision,
            "revision_round": round_no + 1,
            "open_questions": [],
            "artifacts": [
                Artifact(
                    kind="review",
                    role="reviewer",
                    content=decision.feedback,
                    round=round_no,
                    extra={"focus_check": check.model_dump(), **decision.model_dump()},
                )
            ],
            "events": [
                Event(
                    type="reviewer_completed",
                    role="reviewer",
                    round=round_no,
                    payload={"decision": decision.decision, "two_call": True},
                )
            ],
        }

    return reviewer_two_call
