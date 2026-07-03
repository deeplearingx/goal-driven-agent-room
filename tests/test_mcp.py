"""MCP adapter: load a real MCP server's tools into the Registry (offline).

Spins up the tiny `_mcp_test_server.py` as a stdio subprocess — a real MCP
round-trip, no network — and checks the tool loads, registers, and runs.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from agent_room.tools.mcp import register_mcp_tools
from agent_room.tools.registry import Registry

_SERVER = str(Path(__file__).resolve().parent / "_mcp_test_server.py")


def _connections() -> dict[str, dict[str, object]]:
    return {
        "test": {
            "command": sys.executable,
            "args": [_SERVER],
            "transport": "stdio",
        }
    }


@pytest.mark.asyncio
async def test_register_mcp_tools_loads_and_runs() -> None:
    reg = Registry()
    names = await register_mcp_tools(reg, _connections())

    assert "add" in names
    entry = reg.get("add")
    assert entry.toolset == "mcp"

    result = await entry.tool.ainvoke({"a": 2, "b": 3})
    assert "5" in str(result)
