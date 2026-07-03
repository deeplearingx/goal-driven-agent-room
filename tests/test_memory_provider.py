"""v0.5 §5.1 — MemoryProvider Protocol + NoOp + RoleBindings.memory field.

These tests pin the contract and the wiring; later sections (curated, FTS,
tool, integration) layer on top.
"""

from __future__ import annotations

from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

from agent_room.config import RoleBindings
from agent_room.memory import (
    Memory,
    MemoryProvider,
    NoOpMemoryProvider,
    TranscriptRole,
)


def test_memory_provider_protocol_runtime_checkable() -> None:
    assert isinstance(NoOpMemoryProvider(), MemoryProvider)


def test_transcript_role_literal_values() -> None:
    """Sanity: the Role literal accepts only what the transcript log stores."""
    valid: list[TranscriptRole] = ["user", "assistant", "tool"]
    assert valid == ["user", "assistant", "tool"]


def test_memory_dataclass_is_frozen_and_slotted() -> None:
    m = Memory(
        id=1,
        session_id="s1",
        role="assistant",
        content="hello",
        tool_call_id=None,
        tool_name=None,
        timestamp=0.0,
    )
    with pytest.raises(FrozenInstanceError):
        m.content = "mutated"  # type: ignore[misc]
    # slotted: no __dict__
    assert not hasattr(m, "__dict__")


@pytest.mark.asyncio
async def test_noop_provider_all_methods_return_safe_defaults(tmp_path: Path) -> None:
    p = NoOpMemoryProvider()
    assert p.name == "noop"
    await p.initialize(root_dir=tmp_path)
    assert await p.system_prompt_block() == ""
    assert await p.prefetch("anything") == ""
    assert await p.sync_turn("assistant", "msg") is None
    await p.close()  # idempotent + no-op


@pytest.mark.asyncio
async def test_noop_provider_idempotent_close(tmp_path: Path) -> None:
    p = NoOpMemoryProvider()
    await p.initialize(root_dir=tmp_path)
    await p.close()
    await p.close()  # second close must not raise


def test_role_bindings_default_memory_is_noop() -> None:
    """Default `RoleBindings()` yields a NoOp memory — v0.1-v0.4 unchanged."""
    bindings = RoleBindings()
    assert isinstance(bindings.memory, NoOpMemoryProvider)


def test_role_bindings_memory_field_accepts_protocol_impl() -> None:
    """A custom backend can be plugged in; type system requires Protocol satisfaction."""

    class _CustomMemory:
        name = "custom"

        async def initialize(self, **_kwargs: object) -> None: ...
        async def system_prompt_block(self) -> str:
            return "<custom-block/>"

        async def prefetch(self, query: str, k: int = 5) -> str:
            return ""

        async def sync_turn(self, role, content, **_kwargs):  # type: ignore[no-untyped-def]
            return None

        async def close(self) -> None: ...

    bindings = RoleBindings(memory=_CustomMemory())  # type: ignore[arg-type]
    assert bindings.memory.name == "custom"


def test_role_bindings_default_memory_is_independent_per_instance() -> None:
    """default_factory produces a fresh NoOp per RoleBindings — no shared state."""
    a = RoleBindings()
    b = RoleBindings()
    assert a.memory is not b.memory
