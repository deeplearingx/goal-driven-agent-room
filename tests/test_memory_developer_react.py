"""v0.5 §5.5 — `developer_react` × MemoryProvider integration.

Pins:
1. NoOp memory: bit-for-bit identical to v0.4 (no extra system block, no
   sync calls). v0.4 tests already cover this implicitly; this file adds
   explicit assertions on the SystemMessage shape.
2. FileFtsMemoryProvider wired up: curated `system_prompt_block()` is
   appended to the SystemMessage on first entry; transcript prefetch is
   wrapped in `<memory-context>` fence and appended too.
3. `sync_turn("assistant", ...)` is invoked after every LLM response (fire-
   and-forget, but observable via a counting fake provider).
4. Memory injection happens once on first entry, NOT on ReAct re-entry —
   protects prefix-cache.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest
from langchain_core.messages import SystemMessage, ToolMessage

from agent_room.config import RoleBindings
from agent_room.memory import NoOpMemoryProvider
from agent_room.memory.file_fts import FileFtsMemoryProvider
from agent_room.memory.provider import TranscriptRole
from agent_room.roles.developer_react import make_developer_react
from tests.test_context_react_integration import _CapturingScriptedLLM


class _CountingMemory:
    """Fake provider that records every call. Used to verify call timing
    without standing up a real SQLite/FTS5 backend."""

    name = "counting"

    def __init__(self, *, system_block: str = "", prefetch_text: str = "") -> None:
        self._system_block = system_block
        self._prefetch_text = prefetch_text
        self.sync_calls: list[tuple[TranscriptRole, str]] = []
        self.prefetch_calls: list[str] = []

    async def initialize(self, **_kwargs: Any) -> None:
        return None

    async def system_prompt_block(self) -> str:
        return self._system_block

    async def prefetch(self, query: str, k: int = 5) -> str:
        self.prefetch_calls.append(query)
        return self._prefetch_text

    async def sync_turn(
        self,
        role: TranscriptRole,
        content: str,
        *,
        tool_call_id: str | None = None,
        tool_name: str | None = None,
    ) -> None:
        self.sync_calls.append((role, content))

    async def close(self) -> None:
        return None


def _make_state(title: str = "T", description: str = "do the thing") -> dict:
    return {
        "title": title,
        "description": description,
        "plan": "",
        "user_directives": [],
        "dev_messages": [],
        "dev_round": 0,
        "revision_round": 0,
        "code": "",
        "events": [],
        "artifacts": [],
    }


@pytest.mark.asyncio
async def test_noop_memory_does_not_alter_system_prompt() -> None:
    """Default NoOp keeps SystemMessage unchanged — v0.4 contract."""
    llm = _CapturingScriptedLLM(script=["final answer"])
    bindings = RoleBindings(developer=llm, memory=NoOpMemoryProvider())
    node = make_developer_react(bindings, tools=[])

    state = _make_state()
    await node(state)

    seen = llm.observed_inputs[0]
    sys_msg = seen[0]
    assert isinstance(sys_msg, SystemMessage)
    # Must NOT contain the memory-context fence or curated section markers.
    assert "<memory-context>" not in str(sys_msg.content)
    assert "## Memory" not in str(sys_msg.content)
    assert "## User preferences" not in str(sys_msg.content)


@pytest.mark.asyncio
async def test_curated_block_appended_to_system_prompt_on_first_entry() -> None:
    mem = _CountingMemory(
        system_block="## User preferences\n\nprefers Python 3.11+",
        prefetch_text="",
    )
    llm = _CapturingScriptedLLM(script=["done"])
    bindings = RoleBindings(developer=llm, memory=mem)  # type: ignore[arg-type]
    node = make_developer_react(bindings, tools=[])

    await node(_make_state())

    sys_content = str(llm.observed_inputs[0][0].content)
    assert "prefers Python 3.11+" in sys_content
    assert "## User preferences" in sys_content


@pytest.mark.asyncio
async def test_prefetch_block_appended_with_memory_context_fence() -> None:
    mem = _CountingMemory(
        system_block="",
        prefetch_text="<memory-context>\n[System note: ...]\n\n[user @ s] prior fact about widgets\n</memory-context>",
    )
    llm = _CapturingScriptedLLM(script=["done"])
    bindings = RoleBindings(developer=llm, memory=mem)  # type: ignore[arg-type]
    node = make_developer_react(bindings, tools=[])

    await node(_make_state(description="ship widget feature"))

    sys_content = str(llm.observed_inputs[0][0].content)
    assert "<memory-context>" in sys_content
    assert "prior fact about widgets" in sys_content


@pytest.mark.asyncio
async def test_prefetch_query_is_user_message_text() -> None:
    mem = _CountingMemory()
    llm = _CapturingScriptedLLM(script=["done"])
    bindings = RoleBindings(developer=llm, memory=mem)  # type: ignore[arg-type]
    node = make_developer_react(bindings, tools=[])

    await node(_make_state(description="implement OAuth2 token refresh"))

    assert len(mem.prefetch_calls) == 1
    assert "OAuth2" in mem.prefetch_calls[0] or "oauth" in mem.prefetch_calls[0].lower()


@pytest.mark.asyncio
async def test_sync_turn_called_for_user_query_and_each_assistant_response() -> None:
    mem = _CountingMemory()
    llm = _CapturingScriptedLLM(script=["final code: foo"])
    bindings = RoleBindings(developer=llm, memory=mem)  # type: ignore[arg-type]
    node = make_developer_react(bindings, tools=[])

    await node(_make_state())
    # sync_turn fires under create_task — yield once so the loop runs them.
    await asyncio.sleep(0)

    # Expect: 1 user-query sync + 1 assistant-response sync = 2 calls.
    assert len(mem.sync_calls) >= 2
    roles = [r for r, _ in mem.sync_calls]
    assert "user" in roles
    assert "assistant" in roles
    assistant_payloads = [c for r, c in mem.sync_calls if r == "assistant"]
    assert any("final code: foo" in c for c in assistant_payloads)


@pytest.mark.asyncio
async def test_memory_injection_happens_only_on_first_entry() -> None:
    """ReAct re-entry must not re-inject memory — the existing system prompt
    in `dev_messages` is reused as-is so prefix-cache stays hot."""
    mem = _CountingMemory(system_block="curated fact", prefetch_text="")
    # Two responses: one tool_call, one final.
    llm = _CapturingScriptedLLM(
        script=[
            [{"id": "c1", "name": "noop_tool", "args": {}, "type": "tool_call"}],
            "done",
        ]
    )
    bindings = RoleBindings(developer=llm, memory=mem)  # type: ignore[arg-type]
    node = make_developer_react(bindings, tools=[])

    # First call: empty dev_messages → memory injected.
    state = _make_state()
    update1 = await node(state)
    state = {**state, **update1}

    # Simulate ToolNode appending a ToolMessage so the loop continues.
    tool_msg = ToolMessage(content="noop result", tool_call_id="c1")
    state["dev_messages"] = [*state["dev_messages"], tool_msg]
    state["dev_round"] = 1

    # Second call: dev_messages non-empty → memory should NOT be injected again.
    await node(state)

    # Only one prefetch call total.
    assert len(mem.prefetch_calls) == 1
    # Both LLM invocations saw identical SystemMessage (reused, not regenerated).
    sys1 = str(llm.observed_inputs[0][0].content)
    sys2 = str(llm.observed_inputs[1][0].content)
    assert sys1 == sys2


@pytest.mark.asyncio
async def test_real_provider_end_to_end_curated_visible(tmp_path: Path) -> None:
    """Smoke test with the real FileFtsMemoryProvider: write a curated fact in
    one node-invocation, then verify a *fresh* provider/session sees it in the
    system prompt."""
    # Session 1: write a fact via the curated layer directly (simulates a
    # prior session's memory tool call).
    p1 = FileFtsMemoryProvider()
    await p1.initialize(
        root_dir=tmp_path,
        db_path=str(tmp_path / "x.db"),
        session_id="sess-1",
    )
    await p1.add_curated("user", "always uses Python 3.11+")
    await p1.close()

    # Session 2: fresh provider re-reads disk → curated block should appear in
    # the developer's first SystemMessage.
    p2 = FileFtsMemoryProvider()
    await p2.initialize(
        root_dir=tmp_path,
        db_path=str(tmp_path / "x.db"),
        session_id="sess-2",
    )

    llm = _CapturingScriptedLLM(script=["ack"])
    bindings = RoleBindings(developer=llm, memory=p2)
    node = make_developer_react(bindings, tools=[])
    await node(_make_state())

    sys_content = str(llm.observed_inputs[0][0].content)
    assert "always uses Python 3.11+" in sys_content
    await p2.close()
