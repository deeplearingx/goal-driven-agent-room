"""Composite curated-file and transcript-memory provider."""

from __future__ import annotations

from pathlib import Path

from agent_room.memory.curated import CuratedFileStore, CuratedTarget
from agent_room.memory.embedding import EmbeddingBackend
from agent_room.memory.fts import TranscriptStore, render_snippets
from agent_room.memory.prompt import build_memory_context_block
from agent_room.memory.provider import TranscriptRole
from agent_room.memory.vector_index import VectorIndex


class FileFtsMemoryProvider:
    name = "file_fts"

    def __init__(
        self,
        *,
        embedding: EmbeddingBackend | None = None,
        vector_index: VectorIndex | None = None,
    ) -> None:
        self._embedding = embedding
        self._vector_index = vector_index
        self._curated = CuratedFileStore()
        # Construct eagerly because health/config tests inspect the selected
        # backend before the graph lifecycle initializes the database.
        self._transcript = TranscriptStore(embedding=embedding, vector_index=vector_index)

    async def initialize(
        self, *, root_dir: Path, db_path: str | None = None, session_id: str = ""
    ) -> None:
        await self._curated.initialize(root_dir)
        await self._transcript.initialize(
            db_path or str(root_dir / "agent_room.db"), session_id=session_id
        )

    async def system_prompt_block(self) -> str:
        return self._curated.system_prompt_block()

    async def prefetch(self, query: str, k: int = 5) -> str:
        return build_memory_context_block(render_snippets(await self._transcript.search(query, k)))

    async def sync_turn(
        self,
        role: TranscriptRole,
        content: str,
        *,
        tool_call_id: str | None = None,
        tool_name: str | None = None,
    ) -> None:
        await self._transcript.append(
            role, content, tool_call_id=tool_call_id, tool_name=tool_name
        )

    async def add_curated(self, target: CuratedTarget, content: str) -> None:
        await self._curated.add(target, content)

    async def replace_curated(
        self, target: CuratedTarget, old_substring: str, content: str
    ) -> None:
        await self._curated.replace(target, old_substring, content)

    async def remove_curated(self, target: CuratedTarget, substring: str) -> None:
        await self._curated.remove(target, substring)

    def live_curated_text(self, target: CuratedTarget) -> str:
        return self._curated.live_text(target)

    async def close(self) -> None:
        await self._transcript.close()
