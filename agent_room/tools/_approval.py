"""Approval-mode tool wrapper — v0.3.x §3.3 续.

Transforms `tool_mode: approval` from a build-time NotImplementedError into a
runtime human-in-the-loop gate. Each tool call inside the developer ReAct
subgraph halts the graph via `langgraph.types.interrupt()` and surfaces a
structured "approve this tool call?" payload to the client. The client
resumes with `service.resume(task_id, decision, at_node="tool_call")`,
which forwards a `Command(resume=...)` back into the same node.

Decision shape:
  - `"approve"` (or omitted)             — run the tool normally.
  - `"deny"`                             — synthesize a ToolMessage saying
                                           the call was denied; LLM sees
                                           that on its next turn.
  - `{"action": "approve"}`              — same as "approve".
  - `{"action": "deny", "message": "X"}` — custom denial body in the
                                           synthetic ToolMessage.

Approval mode serializes multi-tool-call AIMessages before they reach this
wrapper. Consequently there is exactly one interrupt and one decision per
approval cycle; a single approval can never authorize an implicit batch.

Why a wrapper, not a parallel approval-node:
  - Re-uses ToolNode's argument validation, error handling, parallel
    dispatch, and ToolMessage formatting verbatim.
  - The interrupt payload sits naturally next to `req.tool_call` in
    `awrap_tool_call`, so the snapshot the user sees matches what
    LangGraph would have executed.
  - No extra graph topology; the existing developer-tools edge keeps its
    shape.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from langchain_core.messages import ToolMessage
from langgraph.types import interrupt

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from langgraph.prebuilt.tool_node import ToolCallRequest
    from langgraph.types import Command


_APPROVE_TOKENS = frozenset({"approve", "approved", "yes", "y", "ok"})
_DENY_TOKENS = frozenset({"deny", "denied", "no", "n", "reject", "rejected"})


def _classify_decision(decision: Any) -> tuple[str, str]:
    """Reduce arbitrary resume values to ('approve' | 'deny', message).

    Strings get matched against allow/deny token sets case-insensitively.
    Dicts read `action` (required) and `message` (optional, deny only).
    Anything else defaults to deny with a generic refusal — the conservative
    choice when a misformatted decision arrives.
    """
    if isinstance(decision, dict):
        action = str(decision.get("action", "")).lower()
        if action in _APPROVE_TOKENS:
            return "approve", ""
        if action in _DENY_TOKENS:
            return "deny", str(decision.get("message", "")) or "Tool call denied by reviewer."
        return "deny", f"Unknown decision action: {action!r}. Treating as denied."
    if isinstance(decision, str):
        token = decision.strip().lower()
        if token in _APPROVE_TOKENS:
            return "approve", ""
        if token in _DENY_TOKENS:
            return "deny", "Tool call denied by reviewer."
    return "deny", "Tool call denied (no valid decision provided)."


def make_approval_wrapper() -> Callable[
    [ToolCallRequest, Callable[[ToolCallRequest], Awaitable[ToolMessage | Command[Any]]]],
    Awaitable[ToolMessage | Command[Any]],
]:
    """Return an `awrap_tool_call` that halts on `interrupt(...)` per call.

    The wrapper pauses the graph with a payload of:
        {"action": "approve_tool_call",
         "name": <tool name>, "args": <tool args>, "id": <tool_call_id>}
    and resumes when the caller supplies a decision. On `approve` we delegate
    to the original `execute(req)` so the wrapped ToolNode does its normal
    argument validation + invocation. On `deny` we synthesize a
    `ToolMessage(status="error")` so the next agent turn knows the call
    didn't run.
    """

    async def approval_wrapper(
        req: ToolCallRequest,
        execute: Callable[[ToolCallRequest], Awaitable[ToolMessage | Command[Any]]],
    ) -> ToolMessage | Command[Any]:
        decision = interrupt(
            {
                "action": "approve_tool_call",
                "name": req.tool_call["name"],
                "args": req.tool_call["args"],
                "id": req.tool_call["id"],
            }
        )
        verdict, message = _classify_decision(decision)
        if verdict == "approve":
            return await execute(req)
        return ToolMessage(
            content=message,
            tool_call_id=req.tool_call["id"],
            name=req.tool_call["name"],
            status="error",
        )

    return approval_wrapper
