"""Delivery role: packages the approved code into a final artifact."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage

from agent_room.config import RoleBindings
from agent_room.llm import LangChainTransport, Transport
from agent_room.roles._message import extract_text, format_extra_context
from agent_room.schemas import Artifact, Event
from agent_room.state import TaskState

# §6.9-3 "output" checkpoint: the final handoff text, scanned right before it
# leaves the system. `mode="block"` raises (propagates like BudgetExceededError,
# ending the run); `mode="warn"` records an Event without stopping. Default
# `Guardrail(mode="off")` never scans — bit-for-bit unchanged unless turned on.

DELIVERY_SYSTEM = """You are the delivery role: produce a clean handoff document
combining plan, code, and reviewer notes. Use markdown. Include:
1. Summary
2. Implementation
3. How to run / verify
4. Known limitations"""


def make_delivery(
    bindings: RoleBindings,
    *,
    prompt_override: str | None = None,
    extra_context_keys: list[str] | None = None,
    model: str | None = None,
    transport: Transport | None = None,
) -> Callable[[TaskState], Awaitable[dict[str, Any]]]:
    tx: Transport = transport or LangChainTransport(bindings)
    system_prompt = prompt_override or DELIVERY_SYSTEM
    guardrail = bindings.guardrail

    async def delivery(state: TaskState) -> dict[str, Any]:
        review = state.get("review")
        review_block = ""
        if review:
            review_block = f"\n# Reviewer notes\n{review.feedback}\n"

        prompt = (
            f"# Title\n{state['title']}\n\n"
            f"# Plan\n{state.get('plan', '')}\n\n"
            f"# Code\n{state.get('code', '')}\n"
            f"{review_block}"
        )
        extra = format_extra_context(state, extra_context_keys)
        if extra:
            prompt += "\n" + extra
        msgs = [
            SystemMessage(content=system_prompt),
            HumanMessage(content=prompt),
        ]
        response = await tx.invoke("delivery", msgs, model_override=model)
        content = extract_text(response.message)

        events = [Event(type="delivery_completed", role="delivery")]
        finding = guardrail.check(content, checkpoint="output")
        if finding is not None:  # only reachable in mode="warn" (block raises)
            events.append(
                Event(
                    type="guardrail_triggered",
                    role="delivery",
                    payload={
                        "category": finding.category,
                        "pattern": finding.pattern,
                        "checkpoint": finding.checkpoint,
                    },
                )
            )

        return {
            "delivery": content,
            "artifacts": [Artifact(kind="delivery", role="delivery", content=content)],
            "events": events,
        }

    return delivery
