"""A minimal MCP server for the adapter test. Launched as a stdio subprocess.

Not a pytest module (no `test_` functions; underscore-prefixed) — it's the
server end of the offline MCP round-trip in `test_mcp.py`.
"""

from __future__ import annotations

from mcp.server.fastmcp import FastMCP

server = FastMCP("agent-room-test")


@server.tool()
def add(a: int, b: int) -> int:
    """Add two integers."""
    return a + b


if __name__ == "__main__":
    server.run(transport="stdio")
