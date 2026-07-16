"""Developer role: implements the plan, optionally incorporating reviewer feedback."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from agent_room.config import RoleBindings
from agent_room.llm import LangChainTransport, Transport
from agent_room.prompt import DEVELOPER_SYSTEM, developer_prompt_builder
from agent_room.roles._message import extract_text
from agent_room.schemas import Artifact, Event
from agent_room.state import TaskState

__all__ = ["DEVELOPER_SYSTEM", "make_developer"]


def make_developer(
    bindings: RoleBindings,
    *,
    prompt_override: str | None = None,
    extra_context_keys: list[str] | None = None,
    model: str | None = None,
    transport: Transport | None = None,
) -> Callable[[TaskState], Awaitable[dict[str, Any]]]:
    tx: Transport = transport or LangChainTransport(bindings)
    system_prompt = prompt_override or DEVELOPER_SYSTEM
    prompt_builder = developer_prompt_builder(
        system_prompt=system_prompt,
        extra_context_keys=extra_context_keys,
    )

    async def developer(state: TaskState) -> dict[str, Any]:
        round_no = state.get("revision_round", 0)
        msgs = prompt_builder.build(state)
        response = await tx.invoke("developer", msgs, model_override=model)
        code = extract_text(response.message)
        return {
            "code": code,
            "artifacts": [Artifact(kind="code", role="developer", content=code, round=round_no)],
            "events": [Event(type="developer_completed", role="developer", round=round_no)],
        }

    return developer
