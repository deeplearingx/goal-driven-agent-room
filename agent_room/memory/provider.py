"""Memory provider contracts shared by the runtime and persistence backends."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Protocol, runtime_checkable

TranscriptRole = Literal["user", "assistant", "tool"]


@dataclass(frozen=True, slots=True)
class Memory:
    id: int
    session_id: str
    role: TranscriptRole
    content: str
    tool_call_id: str | None
    tool_name: str | None
    timestamp: float


@runtime_checkable
class MemoryProvider(Protocol):
    name: str

    async def initialize(
        self, *, root_dir: Path, db_path: str | None = None, session_id: str = ""
    ) -> None: ...

    async def system_prompt_block(self) -> str: ...
    async def prefetch(self, query: str, k: int = 5) -> str: ...

    async def sync_turn(
        self,
        role: TranscriptRole,
        content: str,
        *,
        tool_call_id: str | None = None,
        tool_name: str | None = None,
    ) -> None: ...

    async def close(self) -> None: ...


class NoOpMemoryProvider:
    name = "noop"

    async def initialize(
        self, *, root_dir: Path, db_path: str | None = None, session_id: str = ""
    ) -> None:
        return None

    async def system_prompt_block(self) -> str:
        return ""

    async def prefetch(self, query: str, k: int = 5) -> str:
        return ""

    async def sync_turn(
        self,
        role: TranscriptRole,
        content: str,
        *,
        tool_call_id: str | None = None,
        tool_name: str | None = None,
    ) -> None:
        return None

    async def close(self) -> None:
        return None
