"""Tool permission policy — v0.3 §3.3.

Three modes mediate which tools can actually be called from a node's ReAct
loop:

    `read_only`    — only "read-mostly" tools (read_text, glob, custom tools
                     declaring `metadata={"read_only": True}`). Default when
                     a node spec doesn't say otherwise. Maps to PLAN's
                     "deny-by-default" stance for shell + writes.

    `unrestricted` — all tools the spec listed are passed through. Use this
                     in trusted dev environments and CI fixtures.

    `approval`     — all tools the spec listed are passed through, but each
                     call halts the graph via `langgraph.types.interrupt()`
                     so a human can approve / deny it. The runtime gate
                     lives in `agent_room.tools._approval.make_approval_wrapper`
                     and gets attached to the ToolNode by the graph builder.
                     Resume via `service.resume(task_id, decision,
                     at_node="tool_call")`.

Classification uses two signals:

1. `tool.metadata.get("read_only")` — explicit author-declared flag. Wins.
2. Built-in known-name table — `read_text` and `glob` are read-only;
   `write_text` and `shell` are not.

Anything else is treated as **write** (conservative). Author of a custom
read-only tool needs to set `metadata={"read_only": True}` to opt in. This
mirrors how Anthropic's own tool-use docs handle scoping: explicit, not
inferred from name patterns.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal, get_args

if TYPE_CHECKING:
    from langchain_core.tools import BaseTool

PermissionMode = Literal["read_only", "approval", "unrestricted"]
ALL_MODES: tuple[PermissionMode, ...] = get_args(PermissionMode)
DEFAULT_MODE: PermissionMode = "read_only"

_BUILTIN_READ_ONLY: frozenset[str] = frozenset({"read_text", "glob"})
_BUILTIN_WRITE: frozenset[str] = frozenset({"write_text", "shell"})


def is_read_only(tool: BaseTool) -> bool:
    """True iff `tool` is safe to call in `read_only` mode.

    Resolution order:
      1. `tool.metadata["read_only"]` if set (explicit author signal)
      2. Built-in name table (read_text / glob → True; write_text / shell → False)
      3. Conservative default: False (assume side effects)
    """
    metadata = getattr(tool, "metadata", None) or {}
    if "read_only" in metadata:
        return bool(metadata["read_only"])
    name = getattr(tool, "name", "")
    if name in _BUILTIN_READ_ONLY:
        return True
    if name in _BUILTIN_WRITE:
        return False
    return False


def apply_policy(tools: list[BaseTool], mode: PermissionMode) -> list[BaseTool]:
    """Return the subset of `tools` allowed under `mode`.

    `read_only`    — keeps only `is_read_only` tools.
    `unrestricted` — returns the input list unchanged.
    `approval`     — returns the input list unchanged. The actual approval
                     gate is enforced at runtime by `make_approval_wrapper`
                     attached to the ToolNode (not by filtering here). This
                     way `approval` lets the user keep listing write/exec
                     tools and decide call-by-call.
    """
    if mode == "approval":
        return list(tools)
    if mode == "unrestricted":
        return list(tools)
    if mode == "read_only":
        return [t for t in tools if is_read_only(t)]
    raise ValueError(f"unknown permission mode: {mode!r}; allowed: {ALL_MODES}")
