"""Resolve `NodeSpec.tools` (list of tool names) → list of `BaseTool` instances.

Lives next to the registry so the failure mode is consistent: an unknown
tool name fails at *graph build time*, not at runtime, and the error message
shows what's actually registered so a typo is easy to spot.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from agent_room.tools.registry import Registry

if TYPE_CHECKING:
    from langchain_core.tools import BaseTool


def resolve_tool_names(names: list[str], registry: Registry) -> list[BaseTool]:
    """Return BaseTool instances for each name in `names`.

    Raises `KeyError` (with the registered names listed) if any name isn't
    registered. Empty input returns an empty list.
    """
    if not names:
        return []
    available = registry.names()
    missing = [n for n in names if n not in available]
    if missing:
        raise KeyError(f"unregistered tool(s): {missing!r}; available: {sorted(available)!r}")
    return [registry.get(n).tool for n in names]
