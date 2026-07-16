"""Curated MEMORY.md and USER.md storage with frozen per-session snapshots."""

from __future__ import annotations

import asyncio
import os
import tempfile
from collections import defaultdict
from pathlib import Path
from typing import Literal

from agent_room.guardrail import INJECTION_PATTERNS

CuratedTarget = Literal["memory", "user"]
_CAPS = {"memory": 2200, "user": 1375}
_LOCKS: defaultdict[str, asyncio.Lock] = defaultdict(asyncio.Lock)


class CuratedRejection(ValueError):
    pass


class CuratedNotFound(LookupError):
    pass


class CuratedFileStore:
    def __init__(self) -> None:
        self._root: Path | None = None
        self._snapshot = ""

    async def initialize(self, root_dir: Path) -> None:
        self._root = root_dir / "memory"
        self._root.mkdir(parents=True, exist_ok=True)
        for target in ("memory", "user"):
            self._path(target).touch(exist_ok=True)
        self._snapshot = self._render_snapshot()

    def system_prompt_block(self) -> str:
        return self._snapshot

    def live_text(self, target: CuratedTarget) -> str:
        return self._path(target).read_text(encoding="utf-8")

    async def add(self, target: CuratedTarget, content: str) -> None:
        clean = _validate(target, content)
        async with _LOCKS[str(self._path(target))]:
            entries = _entries(self.live_text(target))
            if clean not in entries:
                entries.append(clean)
                _validate_total(target, entries)
                _atomic_write(self._path(target), "\n\n".join(entries))

    async def replace(self, target: CuratedTarget, old_substring: str, content: str) -> None:
        clean = _validate(target, content)
        async with _LOCKS[str(self._path(target))]:
            entries = _entries(self.live_text(target))
            matches = [i for i, entry in enumerate(entries) if old_substring in entry]
            if not matches:
                raise CuratedNotFound(f"no entry contains {old_substring!r}")
            if len(matches) != 1:
                raise CuratedNotFound(f"{len(matches)} entries contain {old_substring!r}; expected 1")
            entries[matches[0]] = clean
            _validate_total(target, entries)
            _atomic_write(self._path(target), "\n\n".join(entries))

    async def remove(self, target: CuratedTarget, substring: str) -> None:
        async with _LOCKS[str(self._path(target))]:
            entries = _entries(self.live_text(target))
            matches = [i for i, entry in enumerate(entries) if substring in entry]
            if len(matches) != 1:
                raise CuratedNotFound(f"{len(matches)} entries contain {substring!r}; expected 1")
            del entries[matches[0]]
            _atomic_write(self._path(target), "\n\n".join(entries))

    def _path(self, target: CuratedTarget) -> Path:
        if self._root is None:
            raise RuntimeError("curated store is not initialized")
        return self._root / ("MEMORY.md" if target == "memory" else "USER.md")

    def _render_snapshot(self) -> str:
        user = self.live_text("user").strip()
        memory = self.live_text("memory").strip()
        blocks: list[str] = []
        if user:
            blocks.append(f"## User preferences\n\n{user}")
        if memory:
            blocks.append(f"## Memory\n\n{memory}")
        return "\n\n".join(blocks)


def _validate(target: CuratedTarget, content: str) -> str:
    clean = content.strip()
    if not clean:
        raise CuratedRejection("empty curated content")
    if any(pattern.search(clean) for pattern in INJECTION_PATTERNS):
        raise CuratedRejection("content rejected by threat scan")
    if len(clean) > _CAPS[target]:
        raise CuratedRejection(f"{target} cap is {_CAPS[target]} characters")
    return clean


def _validate_total(target: CuratedTarget, entries: list[str]) -> None:
    if len("\n\n".join(entries)) > _CAPS[target]:
        raise CuratedRejection(f"{target} cap is {_CAPS[target]} characters")


def _entries(text: str) -> list[str]:
    return [part.strip() for part in text.split("\n\n") if part.strip()]


def _atomic_write(path: Path, content: str) -> None:
    fd, temp_name = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as handle:
            handle.write(content)
        os.replace(temp_name, path)
    finally:
        if os.path.exists(temp_name):
            os.unlink(temp_name)
