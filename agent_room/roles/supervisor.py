"""Supervisor node — the goal mode's stuck-strategy decider.

Deliberately NOT a general-purpose router (the earlier agentic-experiment
branch tried that shape; its ADR-0014 documents why free LLM routing hurts
attributability). The goal loop's mechanical routing stays deterministic
(`goal_router`); this node is *invoked by* that deterministic rule — only
after `STUCK_EVERY` consecutive verification failures — to make the one
decision that genuinely needs judgment: keep hammering, change the plan,
escalate to the human, or give up.

`action == "ask_user"` deliberately reuses the ReviewerDecision
`need_user_decision` contract: that decision value's semantic is exactly
"a human has to decide", and the whole existing pause/resume machinery
(halt → status=awaiting_user → `service.resume()` → user_directives) applies
unchanged, including the frontend. Cheaper and better-tested than a parallel
pause path.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage

from agent_room.config import RoleBindings
from agent_room.llm import LangChainTransport, Transport
from agent_room.schemas import Event, ReviewerDecision, StuckDecision
from agent_room.state import TaskState

SUPERVISOR_SYSTEM = """You are the supervisor of an engineering team working \
toward a verifiable goal. The team is STUCK: the objective verification has \
failed several times in a row. You are called exactly once to pick a strategy:

  - continue: the failures show progress (different errors each round, \
fewer failures) — let the developer keep iterating.
  - replan: the failures repeat the same error or the approach looks wrong — \
send it back to the planner for a revised plan.
  - ask_user: the goal itself seems ambiguous, contradictory, or impossible \
as specified — put `question` to the human and pause.
  - abort: the goal is clearly unachievable and asking won't help — stop \
spending budget.

Keep `reasoning` to one or two sentences; it's for the audit trail."""


def make_supervisor(
    bindings: RoleBindings,
    *,
    prompt_override: str | None = None,
    extra_context_keys: list[str] | None = None,
    model: str | None = None,
    transport: Transport | None = None,
) -> Callable[[TaskState], Awaitable[dict[str, Any]]]:
    tx: Transport = transport or LangChainTransport(bindings)
    system_prompt = prompt_override or SUPERVISOR_SYSTEM
    del extra_context_keys  # accepted for factory-signature uniformity; unused

    async def supervisor(state: TaskState) -> dict[str, Any]:
        decision: StuckDecision = await tx.structured(
            "supervisor",
            StuckDecision,
            _build_messages(state, system_prompt),
            model_override=model,
        )
        update: dict[str, Any] = {
            "stuck_decision": decision,
            "events": [
                Event(
                    type="supervisor_decided",
                    role="supervisor",
                    round=state.get("verify_round", 0),
                    payload={"action": decision.action, "reasoning": decision.reasoning},
                )
            ],
        }
        if decision.action == "ask_user":
            update["review"] = ReviewerDecision(
                decision="need_user_decision",
                feedback=decision.question or decision.reasoning,
            )
        return update

    return supervisor


def _build_messages(state: TaskState, system_prompt: str) -> list[Any]:
    verification = state.get("verification")
    failure = (
        f"round {verification.round}, exit={verification.exit_code}\n{verification.output_tail}"
        if verification is not None
        else "(no verification record)"
    )
    summary = "\n".join(
        [
            f"# Goal\n{state.get('title', '')}\n{state.get('description', '')}",
            f"\n# Current plan\n{state.get('plan') or '(none)'}",
            f"\n# Consecutive failed verifications\n{state.get('verify_round', 0)}"
            f" of max {state.get('max_iterations', 10)}",
            f"\n# Latest failure\n{failure}",
        ]
    )
    return [SystemMessage(content=system_prompt), HumanMessage(content=summary)]
