"""`TenantCheckpointerPool` — physical per-tenant persistence isolation (v1.x §6.15).

Each tenant gets its own SQLite file (checkpoints + curated MEMORY.md/USER.md
+ transcript FTS5/vector, all colocated the same way `open_checkpointer`
already colocates them for the single-tenant case) rather than a shared file
with a `tenant_id` filter column. `tenant_id="default"` maps to the server's
original `db_path` unchanged — existing single-tenant deployments see zero
difference. Any other `tenant_id` gets `{base_dir}/tenants/{tenant_id}/agent_room.db`.

This physical-separation choice was made instead of row-level filtering
because it's *stronger* (no query can accidentally cross a WHERE-clause
boundary — there's no shared table to query across) and because it turned
out to require **no changes at all** to `CuratedFileStore`/`TranscriptStore`/
`MemoryProvider` — `open_checkpointer(bindings, db_path)` already derives
`root_dir` from `db_path`, so giving each tenant its own `db_path` isolates
curated files and the transcript SQLite/FTS5/sqlite-vec tables for free.

Trust boundary (also in SECURITY.md — must not be silently assumed):
`agent_room` does not authenticate callers. `tenant_id` is whatever the
caller's request says it is. This pool guarantees that *once told* which
tenant a request belongs to, that tenant's data is physically separate from
every other tenant's — it does not verify *who* is making the request. A
real multi-tenant deployment needs an auth gateway in front that derives
`tenant_id` from a validated credential and forces it into the request,
rather than trusting a client-supplied value.
"""

from __future__ import annotations

import asyncio
from contextlib import AsyncExitStack
from dataclasses import dataclass, replace
from pathlib import Path
from typing import TYPE_CHECKING

from agent_room.graph import open_checkpointer

if TYPE_CHECKING:
    from collections.abc import Callable

    from agent_room.config import RoleBindings
    from agent_room.graph import GraphCompiler
    from agent_room.memory import MemoryProvider

DEFAULT_TENANT_ID = "default"


@dataclass(frozen=True, slots=True)
class TenantEntry:
    compile_with: GraphCompiler
    """This tenant's memory provider — the exact instance `open_checkpointer`
    initialized against this tenant's `root_dir`/`db_path`. Callers building a
    per-task registry must pass *this*, not some other tenant's, into the
    `memory` tool — otherwise a write would land in the wrong tenant's files."""
    memory: MemoryProvider


class TenantCheckpointerPool:
    """Lazily opens (and caches) one `open_checkpointer` per tenant.

    Uses `AsyncExitStack` because the set of tenants isn't known upfront —
    entries are added as tasks for new tenants arrive, and all of them need
    to be closed together at server shutdown.
    """

    def __init__(
        self,
        bindings_factory: Callable[[str, Path | None], RoleBindings],
        base_db_path: str,
    ) -> None:
        self._bindings_factory = bindings_factory
        self._base_db_path = base_db_path
        self._stack = AsyncExitStack()
        self._entries: dict[str, TenantEntry] = {}
        self._lock = asyncio.Lock()

    def db_path_for(self, tenant_id: str) -> str:
        """Where this tenant's `agent_room.db` (+ colocated memory files) live.

        `"default"` keeps the server's original `base_db_path` untouched —
        the back-compat guarantee for existing single-tenant deployments.
        """
        if tenant_id == DEFAULT_TENANT_ID:
            return self._base_db_path
        base_dir = Path(self._base_db_path).resolve().parent
        tenant_dir = base_dir / "tenants" / tenant_id
        tenant_dir.mkdir(parents=True, exist_ok=True)
        return str(tenant_dir / "agent_room.db")

    async def get(self, tenant_id: str = DEFAULT_TENANT_ID) -> TenantEntry:
        if tenant_id in self._entries:
            return self._entries[tenant_id]
        async with self._lock:
            if tenant_id in self._entries:  # re-check: lost a race to open it
                return self._entries[tenant_id]
            db_path = self.db_path_for(tenant_id)
            tenant_dir = None if tenant_id == DEFAULT_TENANT_ID else Path(db_path).resolve().parent
            bindings = self._bindings_factory(tenant_id, tenant_dir)
            compile_with = await self._stack.enter_async_context(
                open_checkpointer(bindings, db_path)
            )
            entry = TenantEntry(compile_with=compile_with, memory=bindings.memory)
            self._entries[tenant_id] = entry
            return entry

    async def aclose(self) -> None:
        await self._stack.aclose()


def clone_bindings_for_tenant(
    base_bindings: RoleBindings, memory_factory: Callable[[Path | None], MemoryProvider]
) -> Callable[[str, Path | None], RoleBindings]:
    """Build a `bindings_factory` for `TenantCheckpointerPool`.

    `budget`/`guardrail`/role model bindings are stateless config, safe to
    share across every tenant's `RoleBindings` — only `memory` needs a fresh,
    tenant-exclusive instance (`FileFtsMemoryProvider` holds a mutable
    connection/file handle internally; two tenants can't share one).

    `tenant_id == "default"` returns `base_bindings` completely unchanged —
    whatever `.memory` the caller already constructed it with (including a
    test-injected fake) — rather than building a second, discarded instance.
    Only non-default tenants get a fresh `memory_factory(tenant_dir)` call.
    """

    def factory(tenant_id: str, tenant_dir: Path | None) -> RoleBindings:
        if tenant_id == DEFAULT_TENANT_ID:
            return base_bindings
        return replace(base_bindings, memory=memory_factory(tenant_dir))

    return factory
