"""v1.x §6.15 — `TenantCheckpointerPool`: physical per-tenant persistence
isolation (own `agent_room.db` + curated/transcript files per tenant,
`tenant_id="default"` unchanged from the pre-multi-tenant path).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from agent_room.memory import FileFtsMemoryProvider, NoOpMemoryProvider
from agent_room.server.tenancy import (
    DEFAULT_TENANT_ID,
    TenantCheckpointerPool,
    clone_bindings_for_tenant,
)
from tests.fakes import bindings_with_fakes


def _memory_factory(tenant_dir: Path | None) -> FileFtsMemoryProvider:
    return FileFtsMemoryProvider()


@pytest.fixture
def base_db_path(tmp_path: Path) -> str:
    return str(tmp_path / "agent_room.db")


# -------------------- db_path_for --------------------


def test_default_tenant_keeps_base_db_path_unchanged(base_db_path: str) -> None:
    bindings = bindings_with_fakes()
    factory = clone_bindings_for_tenant(bindings, _memory_factory)
    pool = TenantCheckpointerPool(factory, base_db_path)
    assert pool.db_path_for(DEFAULT_TENANT_ID) == base_db_path


def test_other_tenant_gets_own_subdirectory(base_db_path: str) -> None:
    bindings = bindings_with_fakes()
    factory = clone_bindings_for_tenant(bindings, _memory_factory)
    pool = TenantCheckpointerPool(factory, base_db_path)
    path = pool.db_path_for("tenant-a")
    assert path != base_db_path
    assert "tenants" in Path(path).parts
    assert Path(path).parent.name == "tenant-a"
    assert Path(path).parent.is_dir()  # created eagerly


# -------------------- physical isolation --------------------


@pytest.mark.asyncio
async def test_two_tenants_get_physically_separate_db_files(base_db_path: str) -> None:
    bindings = bindings_with_fakes()
    factory = clone_bindings_for_tenant(bindings, _memory_factory)
    pool = TenantCheckpointerPool(factory, base_db_path)

    entry_a = await pool.get("tenant-a")
    entry_b = await pool.get("tenant-b")

    assert pool.db_path_for("tenant-a") != pool.db_path_for("tenant-b")
    assert Path(pool.db_path_for("tenant-a")).exists()
    assert Path(pool.db_path_for("tenant-b")).exists()
    assert entry_a.memory is not entry_b.memory
    await pool.aclose()


@pytest.mark.asyncio
async def test_default_tenant_reuses_original_bindings_memory(base_db_path: str) -> None:
    """tenant_id="default" must get exactly the caller's original `.memory`
    instance, not a fresh one — preserves 100% back-compat for callers that
    inject their own memory provider (e.g. tests)."""
    original_memory = NoOpMemoryProvider()
    bindings = bindings_with_fakes()
    bindings.memory = original_memory
    factory = clone_bindings_for_tenant(bindings, _memory_factory)
    pool = TenantCheckpointerPool(factory, base_db_path)

    entry = await pool.get(DEFAULT_TENANT_ID)
    assert entry.memory is original_memory
    await pool.aclose()


@pytest.mark.asyncio
async def test_get_is_cached_not_reopened(base_db_path: str) -> None:
    bindings = bindings_with_fakes()
    factory = clone_bindings_for_tenant(bindings, _memory_factory)
    pool = TenantCheckpointerPool(factory, base_db_path)

    entry1 = await pool.get("tenant-a")
    entry2 = await pool.get("tenant-a")
    assert entry1 is entry2
    await pool.aclose()


@pytest.mark.asyncio
async def test_aclose_closes_every_opened_tenant(base_db_path: str) -> None:
    bindings = bindings_with_fakes()
    factory = clone_bindings_for_tenant(bindings, _memory_factory)
    pool = TenantCheckpointerPool(factory, base_db_path)

    await pool.get(DEFAULT_TENANT_ID)
    await pool.get("tenant-a")
    await pool.get("tenant-b")
    await pool.aclose()  # must not raise


@pytest.mark.asyncio
async def test_compile_with_produces_a_working_graph(base_db_path: str) -> None:
    from agent_room.spec import load_preset

    bindings = bindings_with_fakes()
    factory = clone_bindings_for_tenant(bindings, _memory_factory)
    pool = TenantCheckpointerPool(factory, base_db_path)

    entry = await pool.get("tenant-a")
    graph = entry.compile_with(load_preset("full"), None)
    config = {"configurable": {"thread_id": "t1"}}
    result = await graph.ainvoke(
        {"title": "t", "description": "d", "max_revisions": 2}, config=config
    )
    assert result["review"].decision == "approved"
    await pool.aclose()
