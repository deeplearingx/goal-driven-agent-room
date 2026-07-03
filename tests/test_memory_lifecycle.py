"""Regression: the graph builder owns the memory provider's lifecycle.

Discovered via the memory ablation: `bindings.memory.initialize()` was never
called anywhere in the service/graph flow, so a configured provider silently
returned nothing. `build_with_sqlite_checkpointer` must initialize on enter and
close on exit.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from agent_room.graph import build_with_sqlite_checkpointer
from tests.fakes import bindings_with_fakes


class _SpyMemory:
    def __init__(self) -> None:
        self.init_root: Path | None = None
        self.init_db: str | None = None
        self.closed = False

    async def initialize(self, *, root_dir, db_path=None, session_id=""):  # type: ignore[no-untyped-def]
        self.init_root = root_dir
        self.init_db = db_path

    async def system_prompt_block(self) -> str:
        return ""

    async def prefetch(self, query: str, k: int = 5) -> str:
        return ""

    async def sync_turn(self, *args, **kwargs):  # type: ignore[no-untyped-def]
        return None

    async def close(self) -> None:
        self.closed = True


@pytest.mark.asyncio
async def test_sqlite_builder_initializes_and_closes_memory(tmp_path) -> None:
    spy = _SpyMemory()
    bindings = bindings_with_fakes()
    bindings.memory = spy  # type: ignore[assignment]
    db = str(tmp_path / "agent.db")

    async with build_with_sqlite_checkpointer(bindings, db_path=db):
        assert spy.init_db == db  # transcript reuses the checkpointer db
        assert spy.init_root == tmp_path  # curated files live next to the db
        assert spy.closed is False

    assert spy.closed is True  # closed on context exit
