"""MCP (Model Context Protocol) client adapter.

MCP is the 2026 de-facto standard for connecting agents to external tools. This
adapter connects to one or more MCP servers, loads their tools as LangChain
`BaseTool`s, and registers them into agent-room's `Registry` — so any role uses
MCP tools exactly like the built-in ones, and the whole MCP tool ecosystem
becomes available.

We don't reimplement the protocol: we borrow LangChain's MCP adapter and wire it
to our registry (ADR-0007 — borrow, stay thin). It's an **optional** dependency
so core agent-room stays lightweight:

    pip install 'agent-room[mcp]'

Example:

    reg = default_registry()
    await register_mcp_tools(reg, {
        "fs": {"command": "npx", "args": ["-y", "@modelcontextprotocol/server-filesystem", "/data"],
               "transport": "stdio"},
    })
    # reg now holds the filesystem server's tools, usable by any role.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, cast

from agent_room.tools.registry import Registry, ToolEntry

if TYPE_CHECKING:
    from langchain_core.tools import BaseTool


async def load_mcp_tools(connections: dict[str, dict[str, Any]]) -> list[BaseTool]:
    """Connect to the configured MCP server(s) and return their tools.

    `connections` is the `langchain-mcp-adapters` config: a map of server name →
    connection (e.g. `{"transport": "stdio", "command": ..., "args": [...]}` or
    `{"transport": "streamable_http", "url": ...}`). Tools are stateless — each
    invocation opens its own short session — so there's no lifecycle to own here.
    """

    try:
        from langchain_mcp_adapters.client import MultiServerMCPClient
    except ImportError as exc:  # pragma: no cover - optional dependency
        raise ImportError("MCP support requires extra deps: pip install 'agent-room[mcp]'") from exc

    # `connections` is pass-through user config; the adapter validates the shape.
    client = MultiServerMCPClient(cast("Any", connections))
    return await client.get_tools()


async def register_mcp_tools(
    registry: Registry,
    connections: dict[str, dict[str, Any]],
    *,
    toolset: str = "mcp",
) -> list[str]:
    """Load MCP tools and register each into `registry`. Returns the tool names."""

    tools = await load_mcp_tools(connections)
    for tool in tools:
        registry.register(ToolEntry(name=tool.name, toolset=toolset, tool=tool))
    return [tool.name for tool in tools]
