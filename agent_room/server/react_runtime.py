"""Server-side wiring for the tool-using developer (ReAct).

The default server graph (`full_react`) grants the developer the four built-in
tools so the pixel UI's tool badges light up. Tools are a foothold for an LLM to
touch the host, so this module is the single place that defines the **security
envelope** and keeps the graph spec and the tool registry consistent with it.

Envelope (all enforced here, not in the YAML preset):

1. **Filesystem confinement** — `read_text` / `write_text` / `glob` are rooted
   at `settings.workspace_dir`. `resolve_within_root` rejects `..` traversal,
   absolute escapes, and symlink escapes (see tools/_safety.py).
2. **Shell allowlist (deny-by-default)** — `shell` only runs commands whose head
   token is in `settings.shell_allowlist` (default: pytest/python/python3/ruff).
   An empty allowlist drops the shell tool entirely, and we drop `shell` from the
   spec too so the build doesn't reference a missing tool.
3. **Shell execution confinement** — the shell's `cwd` is pinned to the same
   workspace, so commands can't run against the repo or the home directory.
4. **Timeout** — every shell call is bounded (30s) to stop runaway processes.
5. **Bounded ReAct loop** — `max_dev_rounds` (from the preset) caps tool-calling
   so a confused developer can't loop forever / burn tokens without end.

`tool_mode` (default `unrestricted`) decides whether write/shell survive the
permission filter. Set `AGENT_ROOM_TOOL_MODE=approval` for a per-call
human-in-the-loop gate (resume via `/tasks/{id}/resume`), or `read_only` to keep
only `read_text`/`glob`.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

from agent_room.config import Settings
from agent_room.memory import (
    FileFtsMemoryProvider,
    MemoryProvider,
    NoOpEmbeddingBackend,
    NoOpMemoryProvider,
)
from agent_room.spec import GraphSpec, load_preset
from agent_room.tools import Registry, ToolEntry, register_builtin_tools
from agent_room.tools.mcp import load_mcp_tools

if TYPE_CHECKING:
    from langchain_core.tools import BaseTool

SHELL_TIMEOUT_S = 30.0


def workspace_dir(settings: Settings) -> Path:
    """Resolved sandbox workspace (where the developer's tools read/write).
    Created if absent so the file-browser endpoints can always list it."""
    ws = Path(settings.workspace_dir).resolve()
    ws.mkdir(parents=True, exist_ok=True)
    return ws


def task_workspace(settings: Settings, task_id: str) -> Path:
    """Per-task subdir of the workspace, so tasks don't overwrite each other's
    files. `task_id` is sanitized to a single path segment (no traversal)."""
    safe = Path(task_id).name or "task"
    ws = workspace_dir(settings) / safe
    ws.mkdir(parents=True, exist_ok=True)
    return ws


def build_server_memory(settings: Settings, *, tenant_dir: Path | None = None) -> MemoryProvider:
    """The memory backend the server binds. `FileFtsMemoryProvider` persists
    curated facts + a transcript log; `NoOpMemoryProvider` keeps zero state.

    The *same* instance must be shared between `RoleBindings.memory` (which
    renders the system-prompt block) and the `memory` tool in the registry
    (which writes facts) — otherwise a write wouldn't be visible next session.

    `tenant_dir` (v1.x §6.15) scopes the Qdrant vector backend (if enabled) to
    one tenant's own embedded storage path / collection name — irrelevant for
    the sqlite backend, which is already physically isolated per tenant by
    `TenantCheckpointerPool` giving each tenant its own `db_path`.
    """
    if settings.memory_enabled:
        return FileFtsMemoryProvider(
            embedding=settings.embedding_backend(),
            vector_index=settings.vector_index(tenant_dir=tenant_dir),
        )
    return NoOpMemoryProvider()


async def load_server_mcp_tools(settings: Settings) -> list[BaseTool]:
    """Connect to `settings.mcp_servers` (if any) and return their tools.

    Empty (default, unconfigured) → `[]`, zero behavior change — same NoOp-default
    shape as memory/shell. Connection/config errors propagate (fail loud at
    startup rather than silently running with a missing tool ecosystem); called
    once from the server lifespan, and the same tool instances are shared across
    every per-task registry (MCP tools open their own short session per call, so
    they're safe to reuse — see agent_room/tools/mcp.py).
    """
    if not settings.mcp_servers:
        return []
    return await load_mcp_tools(settings.mcp_servers)


def _available_tool_names(settings: Settings, mcp_tool_names: tuple[str, ...] = ()) -> set[str]:
    """Tool names the server registry will actually contain, given the envelope.

    `shell` is present only when an allowlist is configured; `memory` only when
    cross-session memory is enabled; the fs tools are always available; MCP tool
    names are whatever `load_server_mcp_tools` discovered (empty if unconfigured).
    """
    names = {"glob", "read_text", "write_text"}
    if settings.shell_allowlist:
        names.add("shell")
    if settings.memory_enabled:
        names.add("memory")
    names.update(mcp_tool_names)
    return names


def build_server_registry(
    settings: Settings,
    *,
    memory_provider: FileFtsMemoryProvider | None = None,
    workspace_override: Path | None = None,
    mcp_tools: list[BaseTool] | None = None,
) -> Registry:
    """Build a registry whose tools are confined to the workspace.

    Creates the workspace dir if absent (the fs tools require an existing root).
    Shell is registered only when an allowlist is set, and is pinned to the
    workspace cwd with a timeout. `memory_provider` (when given) registers the
    `memory` tool against that exact instance so writes reach the bound provider.
    `workspace_override` roots the tools at a per-task subdir for isolation.
    `mcp_tools` (when given) registers each under the `"mcp"` toolset — they
    pass through the same `tool_mode` permission filter as builtin tools (an
    MCP tool with no `read_only` metadata is conservatively treated as a
    write/exec tool, so `read_only` mode drops it, matching every other unknown
    tool — see `tools/policy.py::is_read_only`).
    """
    workspace = workspace_override or workspace_dir(settings)
    workspace.mkdir(parents=True, exist_ok=True)

    registry = Registry()
    register_builtin_tools(
        registry,
        fs_root=str(workspace),
        # Empty tuple → None → ShellTool not registered (deny-by-default).
        shell_allowlist=list(settings.shell_allowlist) or None,
        shell_cwd=str(workspace),
        shell_timeout_s=SHELL_TIMEOUT_S,
        memory_provider=memory_provider,
        # §6.9-3 "tool_call" checkpoint. `settings.guardrail_mode == "off"`
        # (default) makes this a NoOp inside write_text/shell — zero overhead.
        guardrail=settings.guardrail(),
    )
    for tool in mcp_tools or []:
        registry.register(ToolEntry(name=tool.name, toolset="mcp", tool=tool))
    return registry


def build_server_spec(
    settings: Settings,
    *,
    graph_preset: str | None = None,
    mcp_tool_names: tuple[str, ...] = (),
) -> GraphSpec:
    """Load a preset and reconcile its developer tools with the envelope: drop
    any tool the registry won't contain (e.g. `shell` when the allowlist is
    empty), grant `memory`/MCP tools the preset doesn't declare, and apply the
    configured `tool_mode`.

    `graph_preset` (when given) overrides `settings.graph_preset` for a
    per-request mode choice (workflow vs goal); the caller is responsible for
    allowlisting which presets a client may request. Non-react presets
    (developer declares no tools) pass through untouched.
    """
    spec = load_preset(graph_preset or settings.graph_preset)
    available = _available_tool_names(settings, mcp_tool_names)

    data: dict[str, Any] = spec.model_dump()
    for node in data["nodes"].values():
        if node.get("role") != "developer" or not node.get("tools"):
            continue
        node["tools"] = [t for t in node["tools"] if t in available]
        # The preset doesn't list `memory` or MCP tools; grant them here so the
        # developer can persist facts / use the MCP tool ecosystem.
        if settings.memory_enabled and "memory" not in node["tools"]:
            node["tools"].append("memory")
        for name in mcp_tool_names:
            if name not in node["tools"]:
                node["tools"].append(name)
        if node["tools"]:
            node["tool_mode"] = settings.tool_mode
        else:
            # No tools survived the envelope; strip tool_mode so the NodeSpec
            # validator (tool_mode requires tools) doesn't reject the spec.
            node.pop("tool_mode", None)
    return GraphSpec.model_validate(data)


def describe_tool_envelope(
    settings: Settings, *, mcp_tool_names: tuple[str, ...] = ()
) -> dict[str, Any]:
    """Operator-facing summary of the active envelope (surfaced via /healthz)."""
    return {
        "graph_preset": settings.graph_preset,
        "workspace_dir": str(Path(settings.workspace_dir).resolve()),
        "tool_mode": settings.tool_mode,
        "shell_enabled": bool(settings.shell_allowlist),
        "shell_allowlist": list(settings.shell_allowlist),
        "shell_timeout_s": SHELL_TIMEOUT_S,
        "memory_enabled": settings.memory_enabled,
        "memory_vector": settings.memory_vector and settings.memory_enabled,
        "memory_vector_backend": _describe_embedding_backend(settings),
        "vector_index_backend": settings.vector_backend
        if (settings.memory_vector and settings.memory_enabled)
        else "none",
        "mcp_servers": sorted(settings.mcp_servers),
        "mcp_tools": sorted(mcp_tool_names),
    }


def _describe_embedding_backend(settings: Settings) -> str:
    """Name of the active embedding backend, without constructing one.

    `settings.embedding_backend()` would work but actually loads the real
    ONNX model in the `qdrant` case (real I/O, seconds of latency) — too
    expensive to pay on every `/healthz` call just to report a string.
    """
    if not settings.memory_vector or not settings.memory_enabled:
        return NoOpEmbeddingBackend().name
    if settings.vector_backend == "qdrant":
        return f"fastembed:{settings.embedding_model}"
    return "hashing"
