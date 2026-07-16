"""LangChain tool for explicit curated-memory maintenance."""

from __future__ import annotations

from typing import Literal

from langchain_core.tools import BaseTool
from pydantic import BaseModel, Field

from agent_room.memory.curated import CuratedNotFound, CuratedRejection
from agent_room.memory.file_fts import FileFtsMemoryProvider


class MemoryInput(BaseModel):
    action: Literal["add", "replace", "remove"]
    target: Literal["memory", "user"]
    content: str
    old_substring: str | None = None


class MemoryTool(BaseTool):
    name: str = "memory"
    description: str = "Add, replace, or remove durable curated memory and user preferences."
    args_schema: type[BaseModel] = MemoryInput
    provider: FileFtsMemoryProvider = Field(exclude=True)

    def _run(self, **_: object) -> str:
        raise RuntimeError("memory is async-only")

    async def _arun(
        self,
        action: str,
        target: str,
        content: str,
        old_substring: str | None = None,
        **_: object,
    ) -> str:
        try:
            if action == "add":
                await self.provider.add_curated(target, content)  # type: ignore[arg-type]
            elif action == "replace":
                if not old_substring:
                    raise CuratedRejection("replace requires old_substring")
                await self.provider.replace_curated(target, old_substring, content)  # type: ignore[arg-type]
            else:
                await self.provider.remove_curated(target, content)  # type: ignore[arg-type]
            return f"ok: {action} {target}"
        except CuratedRejection as exc:
            return f"rejected: {exc}"
        except CuratedNotFound as exc:
            return f"not_found: {exc}"
