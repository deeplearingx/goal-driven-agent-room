"""Translates LangGraph stream events into UI-friendly messages.

Equivalent to the TS `event-adapter.ts`: takes raw `astream_events` payloads
and emits normalized dicts for SSE consumers and the CLI.

**SSE event contract** (what a frontend subscribes to on `POST /tasks/stream`).
Every event carries `type`, `role` (planner/developer/reviewer/delivery), and
`task_id`:

    task_started   {task_id}
    node_start     {role}                    — an agent began working
    node_end       {role, summary}           — finished; summary is per-role:
                     planner   {plan_preview}
                     developer {code_chars}
                     reviewer  {decision, round}
                     delivery  {delivery_chars}
    token          {role, text}              — live generation, token by token
    tool_call      {role, tool, args}        — agent invoked a tool (read/shell/…)
    tool_result    {role, tool, preview}     — tool returned
    usage          {role, input_tokens, output_tokens}  — per-call token cost
    task_finished  <full TaskResult>
    task_error     {task_id, error}

Goal mode (PLAN.md §6.16) adds two node-outcome events:

    verification_completed {role: verifier, passed, exit_code, round}
    supervisor_decided     {role: supervisor, action, reasoning}

Server status (`TaskResult.status`) is the 4-state truth
(running / awaiting_user / completed / failed); the 12-label UI states are
derived client-side from status + last event + reviewer decision (ADR-0011).
"""

from __future__ import annotations

from typing import Any

from agent_room.memory.scrubber import StreamingContextScrubber


def format_stream_event(event: dict[str, Any]) -> dict[str, Any] | None:
    """Return a normalized dict for UI display, or None to skip the event."""

    event_type = event.get("event")
    name = event.get("name", "")
    data = event.get("data", {}) or {}

    if event_type == "on_chain_start" and name in {
        "planner",
        "developer",
        "reviewer",
        "delivery",
    }:
        return {
            "type": "node_start",
            "role": name,
            "task_id": event.get("task_id"),
        }

    if event_type == "on_chain_end" and name in {
        "planner",
        "developer",
        "reviewer",
        "delivery",
    }:
        output = data.get("output") or {}
        return {
            "type": "node_end",
            "role": name,
            "task_id": event.get("task_id"),
            "summary": _summarize_node_output(name, output),
        }

    # Goal-mode nodes (PLAN.md §6.16) aren't role nodes in the four-name set
    # above; surface their outcome directly so the UI timeline shows the
    # iteration loop live instead of only in the final snapshot.
    if event_type == "on_chain_end" and name == "verify":
        verification = (data.get("output") or {}).get("verification")
        if verification is None:
            return None  # passthrough (no verify_command) — nothing happened
        return {
            "type": "verification_completed",
            "role": "verifier",
            "task_id": event.get("task_id"),
            "passed": bool(_field(verification, "passed")),
            "exit_code": _field(verification, "exit_code"),
            "round": _field(verification, "round") or 0,
        }

    if event_type == "on_chain_end" and name == "supervisor":
        decision = (data.get("output") or {}).get("stuck_decision")
        if decision is None:
            return None
        return {
            "type": "supervisor_decided",
            "role": "supervisor",
            "task_id": event.get("task_id"),
            "action": _field(decision, "action"),
            "reasoning": _field(decision, "reasoning") or "",
        }

    if event_type == "on_chat_model_stream":
        chunk = data.get("chunk")
        text = ""
        if chunk is not None:
            content = getattr(chunk, "content", None)
            if isinstance(content, str):
                text = content
            elif isinstance(content, list):
                text = "".join(
                    p.get("text", "") if isinstance(p, dict) else str(p) for p in content
                )
        if text:
            return {
                "type": "token",
                "role": _infer_role_from_metadata(event),
                "task_id": event.get("task_id"),
                "text": text,
            }

    # Tool events surface the developer's ReAct actions (read a file, run pytest,
    # write code) — the most "alive" moments for a UI to render.
    if event_type == "on_tool_start":
        return {
            "type": "tool_call",
            "role": _infer_role_from_metadata(event) or "developer",
            "task_id": event.get("task_id"),
            "tool": name,
            "args": _truncate(data.get("input"), 200),
        }

    if event_type == "on_tool_end":
        return {
            "type": "tool_result",
            "role": _infer_role_from_metadata(event) or "developer",
            "task_id": event.get("task_id"),
            "tool": name,
            "preview": _truncate(_tool_output_text(data.get("output")), 200),
        }

    # Per-call token usage for a live cost / "energy" meter.
    if event_type == "on_chat_model_end":
        usage = _usage_from_message(data.get("output"))
        if usage is not None:
            return {
                "type": "usage",
                "role": _infer_role_from_metadata(event),
                "task_id": event.get("task_id"),
                "input_tokens": usage[0],
                "output_tokens": usage[1],
            }

    return None


def _field(obj: Any, key: str) -> Any:
    """Read `key` off a pydantic model OR a serialized dict — astream_events
    may surface a node's returned object either way."""
    if isinstance(obj, dict):
        return obj.get(key)
    return getattr(obj, key, None)


def _truncate(value: Any, limit: int) -> str:
    text = value if isinstance(value, str) else str(value)
    return text if len(text) <= limit else text[:limit] + "…"


def _tool_output_text(output: Any) -> str:
    content = getattr(output, "content", None)
    if content is not None:
        return content if isinstance(content, str) else str(content)
    return str(output) if output is not None else ""


def _usage_from_message(output: Any) -> tuple[int, int] | None:
    meta = getattr(output, "usage_metadata", None)
    if not meta and isinstance(output, dict):
        meta = output.get("usage_metadata")
    if not meta:
        return None
    return int(meta.get("input_tokens", 0) or 0), int(meta.get("output_tokens", 0) or 0)


class StreamEventFormatter:
    """Stateful stream formatter for one live SSE/CLI stream.

    `format_stream_event()` stays stateless for snapshots and unit-level use.
    Live token streams need state because a `<memory-context>` fence can split
    across model chunks; this wrapper keeps one scrubber per task id.
    """

    def __init__(self) -> None:
        self._scrubbers: dict[str, StreamingContextScrubber] = {}
        self._last_token_meta: dict[str, tuple[str | None, str | None]] = {}

    def format(self, event: dict[str, Any]) -> dict[str, Any] | None:
        """Return a normalized event with token text scrubbed."""

        formatted = format_stream_event(event)
        if formatted is None or formatted.get("type") != "token":
            return formatted

        task_id = formatted.get("task_id")
        key = _scrubber_key(task_id)
        scrubber = self._scrubbers.setdefault(key, StreamingContextScrubber())
        text = scrubber.feed(str(formatted.get("text") or ""))
        self._last_token_meta[key] = (
            formatted.get("role"),
            task_id if isinstance(task_id, str) else None,
        )
        if not text:
            return None
        return {**formatted, "text": text}

    def flush(self) -> list[dict[str, Any]]:
        """Flush held non-fence text at stream end.

        Incomplete memory fences are dropped by `StreamingContextScrubber`;
        incomplete ordinary text such as a partial `<memo` prefix is released.
        """

        events: list[dict[str, Any]] = []
        for key, scrubber in list(self._scrubbers.items()):
            text = scrubber.flush()
            if not text:
                continue
            role, task_id = self._last_token_meta.get(key, (None, None))
            events.append(
                {
                    "type": "token",
                    "role": role,
                    "task_id": task_id,
                    "text": text,
                }
            )
        self._scrubbers.clear()
        self._last_token_meta.clear()
        return events


def _summarize_node_output(role: str, output: dict[str, Any]) -> dict[str, Any]:
    if role == "planner":
        plan = output.get("plan") or ""
        return {"plan_preview": plan[:200]}
    if role == "developer":
        code = output.get("code") or ""
        return {"code_chars": len(code)}
    if role == "reviewer":
        review = output.get("review")
        if review is None:
            return {}
        decision = getattr(review, "decision", None) or (
            review.get("decision") if isinstance(review, dict) else None
        )
        return {"decision": decision, "round": output.get("revision_round")}
    if role == "delivery":
        delivery = output.get("delivery") or ""
        return {"delivery_chars": len(delivery)}
    return {}


def _infer_role_from_metadata(event: dict[str, Any]) -> str | None:
    metadata = event.get("metadata") or {}
    tags = metadata.get("tags") or event.get("tags") or []
    for role in ("planner", "developer", "reviewer", "delivery"):
        if role in tags:
            return role
    parents = metadata.get("langgraph_node")
    if parents in {"planner", "developer", "reviewer", "delivery"}:
        return str(parents)
    # Tool nodes are named `<role>_tools` (e.g. `developer_tools`).
    if isinstance(parents, str) and parents.endswith("_tools"):
        base = parents[: -len("_tools")]
        if base in {"planner", "developer", "reviewer", "delivery"}:
            return base
    return None


def _scrubber_key(task_id: Any) -> str:
    if isinstance(task_id, str) and task_id:
        return task_id
    return "__default__"
