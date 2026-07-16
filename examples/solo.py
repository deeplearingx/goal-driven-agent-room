"""Solo preset example: developer node only, no reviewer or delivery.

Run:
    python -m examples.solo

Demonstrates `build_from_spec` with `load_preset("solo")` and a real LLM,
falling back to a fake when no provider is configured (so the file stays
runnable in CI / hooks).
"""

from __future__ import annotations

import asyncio

from langchain_core.language_models.fake_chat_models import FakeListChatModel

from agent_room.config import RoleBindings, load_settings
from agent_room.graph import build_from_spec
from agent_room.schemas import TaskRequest
from agent_room.service import AgentRoomService
from agent_room.spec import load_preset


async def main() -> None:
    settings = load_settings()
    has_provider = bool(
        settings.anthropic_auth_token or settings.anthropic_api_key or settings.openai_api_key
    )

    if has_provider:
        bindings = RoleBindings(settings=settings)
    else:
        # Offline fallback so the example always runs.
        bindings = RoleBindings(
            developer=FakeListChatModel(
                responses=["```python\ndef add(a, b):\n    return a + b\n```"]
            )
        )

    spec = load_preset("solo")
    graph = build_from_spec(spec, bindings)
    service = AgentRoomService(graph)

    result = await service.run(
        TaskRequest(title="add two ints", description="Implement add(a, b) -> int.")
    )

    print(f"status={result.status} rounds={result.rounds}")
    print("\n--- code ---")
    print(result.code)


if __name__ == "__main__":
    asyncio.run(main())
