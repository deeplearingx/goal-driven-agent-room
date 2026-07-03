"""Developer role with ReAct tool-calling loop.

Wired up in `build_uncompiled_from_spec` when a `developer` node has a
non-empty `spec.tools`. Pairs with a sibling `<name>_tools` ToolNode and a
conditional edge driven by `langgraph.prebuilt.tools_condition`.

Loop shape:

    initial entry          ─▶ build [System, Human] ─▶ llm.bind_tools(...) ─┐
    (dev_messages == [])                                                    │
                                                                            ▼
    re-entry (after tools) ─▶ append nothing, just call ─▶ llm.bind_tools──┘
    (dev_messages != [])

When the AIMessage carries no tool_calls we treat that turn as the final
answer: extract its text into `state.code`, emit the `developer_completed`
event, and stop binding tools. `dev_round` increments on every developer-
agent invocation; once it reaches `max_dev_rounds` the next call drops
`bind_tools` to force convergence (the LLM physically cannot emit tool
calls without bound tools).

Returning the AIMessage in `dev_messages` is required: ToolNode reads
`state["dev_messages"][-1].tool_calls` to know what to execute.

Context engine integration (v0.4 §4.5):
The persisted `state["dev_messages"]` is append-only under its `add`
reducer — that's the audit trail. Each turn, before invoking the LLM, we
run `bindings.context_engine.apply(convo)` to bound the *LLM-input*
list. Default `NoOpContextEngine` is identity, so behaviour matches v0.3
exactly. Swap to `WindowedContextEngine(...)` to cap prompt size on long
ReAct sessions.

Memory integration (v0.5 §5.5):
On *first entry* (`dev_messages == []`) we extend the SystemMessage with
two memory layers from `bindings.memory`:

  1. `system_prompt_block()` — frozen curated MEMORY.md / USER.md text
  2. `prefetch(user_query)` — transcript snippets wrapped in
     `<memory-context>` fence

After every LLM call we fire-and-forget `sync_turn("assistant", ...)` so
the response lands in the transcript log for next-session recall. Default
`NoOpMemoryProvider` returns empty strings / does nothing, so v0.4 behavior
is bit-for-bit unchanged unless memory is explicitly wired up.

Guardrail "tool_response" checkpoint (v1.x §6.9-3):
On re-entry, `dev_messages` already holds the `ToolMessage`(s) ToolNode
appended for the AIMessage's tool_calls — the trailing run of consecutive
`ToolMessage`s at the end of `existing`. We scan just that new batch with
`bindings.guardrail` before it's sent to the next LLM call. This is the
highest-value guardrail checkpoint: it catches a malicious/compromised MCP
server trying to prompt-inject via its tool's return value, not just our
own built-in tools. `mode="block"` raises (propagates like any other
exception, ending the run); `mode="warn"` records an `Event` without
stopping. Default `Guardrail(mode="off")` never scans — bit-for-bit
unchanged unless explicitly turned on.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Any

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage, ToolMessage

from agent_room.config import RoleBindings
from agent_room.llm import LangChainTransport, Transport
from agent_room.prompt import DEVELOPER_SYSTEM_WITH_TOOLS, developer_prompt_builder
from agent_room.roles._message import extract_text
from agent_room.schemas import Artifact, Event
from agent_room.state import TaskState

if TYPE_CHECKING:
    from langchain_core.tools import BaseTool

_log = logging.getLogger(__name__)

DEFAULT_MAX_DEV_ROUNDS = 6

__all__ = ["DEFAULT_MAX_DEV_ROUNDS", "DEVELOPER_SYSTEM_WITH_TOOLS", "make_developer_react"]

BUDGET_EXHAUSTED_NOTE = (
    "\n\nNOTE: You've used your full tool budget. Emit the final code now "
    "without calling any more tools."
)


def make_developer_react(
    bindings: RoleBindings,
    *,
    tools: list[BaseTool],
    max_dev_rounds: int = DEFAULT_MAX_DEV_ROUNDS,
    prompt_override: str | None = None,
    extra_context_keys: list[str] | None = None,
    model: str | None = None,
    transport: Transport | None = None,
) -> Callable[[TaskState], Awaitable[dict[str, Any]]]:
    """Build the developer-agent half of a ReAct subgraph.

    Companion node is `ToolNode(tools, messages_key="dev_messages")` wired
    in `build_uncompiled_from_spec`.
    """
    tx: Transport = transport or LangChainTransport(bindings)
    system_prompt = prompt_override or DEVELOPER_SYSTEM_WITH_TOOLS
    context_engine = bindings.context_engine
    memory = bindings.memory
    guardrail = bindings.guardrail
    prompt_builder = developer_prompt_builder(
        system_prompt=system_prompt,
        extra_context_keys=extra_context_keys,
    )

    async def developer_react(state: TaskState) -> dict[str, Any]:
        round_no = state.get("revision_round", 0)
        dev_round = state.get("dev_round", 0)
        existing = state.get("dev_messages") or []

        # §6.9-3 "tool_response" checkpoint — scan the newest tool result(s)
        # (from a built-in tool *or* an MCP server) before they reach the LLM.
        # `mode="block"` raises here, ending the round before any LLM call.
        guardrail_events: list[Event] = []
        for tool_msg in _new_tool_messages(existing):
            finding = guardrail.check(extract_text(tool_msg), checkpoint="tool_response")
            if finding is not None:  # only reachable in mode="warn" (block raises)
                guardrail_events.append(
                    Event(
                        type="guardrail_triggered",
                        role="developer",
                        round=round_no,
                        payload={
                            "category": finding.category,
                            "pattern": finding.pattern,
                            "checkpoint": finding.checkpoint,
                        },
                    )
                )

        # On re-entry from ToolNode `existing` is non-empty; keep iterating.
        # On first entry build the initial [SystemMessage, HumanMessage] pair.
        convo = existing or prompt_builder.build(state)

        # Memory injection happens on first entry only — once the [System,
        # Human, ...] head is in `dev_messages`, it stays bit-for-bit stable
        # across ReAct turns (protects prefix-cache).
        if not existing:
            # Pull the query from `description` (or `title`) directly. The
            # rendered HumanMessage carries `# Plan` / `# Task` markdown that
            # confuses FTS5 (`#` is special) and balloons LIKE substring
            # matching to the entire prompt, dropping recall to zero.
            query = state.get("description") or state.get("title") or ""
            convo = await _inject_memory(convo, memory, query=query)
            # Also seed the transcript with the user query so later sessions
            # can recall it. Best-effort.
            if query:
                _fire_and_forget_sync(memory, "user", query)

        budget_used = dev_round >= max_dev_rounds
        if budget_used:
            # Force convergence: drop bind_tools so the LLM cannot return tool_calls.
            convo = _append_budget_note(convo)

        # Compress the LLM-input only. The persisted `dev_messages` keeps
        # growing under its `add` reducer — this engine bounds the prompt
        # we send each turn, not the audit trail.
        llm_input = await context_engine.apply(convo)

        if budget_used:
            response_msg = (await tx.invoke("developer", llm_input, model_override=model)).message
        else:
            response_msg = (
                await tx.invoke("developer", llm_input, tools=tools, model_override=model)
            ).message
        response = (
            response_msg
            if isinstance(response_msg, AIMessage)
            else AIMessage(content=str(response_msg))
        )

        # Fire-and-forget transcript sync — must not block the LLM path.
        response_text = extract_text(response)
        if response_text:
            _fire_and_forget_sync(memory, "assistant", response_text)

        update: dict[str, Any] = {"dev_round": dev_round + 1}

        # First time through, persist the prompt so ToolNode can re-read it.
        # On re-entry `existing` already holds [System, Human, AI, Tool, ...]
        # and we only contribute the new AIMessage.
        if existing:
            update["dev_messages"] = [response]
        else:
            update["dev_messages"] = [*convo, response]

        events = list(guardrail_events)
        if not response.tool_calls:
            # Final answer turn — extract code + emit completion event.
            code = extract_text(response)
            update["code"] = code
            update["artifacts"] = [
                Artifact(kind="code", role="developer", content=code, round=round_no)
            ]
            events.append(Event(type="developer_completed", role="developer", round=round_no))
        if events:
            update["events"] = events

        return update

    return developer_react


def _new_tool_messages(existing: list[BaseMessage]) -> list[ToolMessage]:
    """The trailing run of `ToolMessage`s at the end of `existing` — the batch
    ToolNode just appended for the last AIMessage's tool_calls. Empty on first
    entry (`existing == []`) or when the tail isn't tool results."""
    out: list[ToolMessage] = []
    for msg in reversed(existing):
        if isinstance(msg, ToolMessage):
            out.append(msg)
        else:
            break
    out.reverse()
    return out


def _append_budget_note(convo: list[BaseMessage]) -> list[BaseMessage]:
    """Return a new conversation list with the budget-exhausted note appended.

    We don't mutate the input — `convo` is part of state and must stay
    immutable so the LangGraph reducer semantics remain correct.
    """
    if not convo:
        return convo
    last = convo[-1]
    if isinstance(last, HumanMessage):
        return [*convo[:-1], HumanMessage(content=str(last.content) + BUDGET_EXHAUSTED_NOTE)]
    return [*convo, HumanMessage(content=BUDGET_EXHAUSTED_NOTE.lstrip())]


async def _inject_memory(convo: list[BaseMessage], memory: Any, *, query: str) -> list[BaseMessage]:
    """Augment the SystemMessage with curated facts + transcript prefetch.

    Returns a new list — does not mutate `convo`. When the provider is
    `NoOpMemoryProvider` both `system_prompt_block()` and `prefetch()` return
    empty strings and this function is a no-op identity transform.

    `query` is the search string for transcript prefetch — pass clean text
    (e.g. `state["description"]`), not the rendered HumanMessage which
    contains `# Plan` / `# Task` markup that breaks FTS5 + LIKE alike.
    """
    if not convo or not isinstance(convo[0], SystemMessage):
        return convo

    curated = await memory.system_prompt_block()
    prefetch = await memory.prefetch(query) if query else ""

    extra_blocks: list[str] = []
    if curated.strip():
        extra_blocks.append(curated.strip())
    if prefetch.strip():
        extra_blocks.append(prefetch.strip())
    if not extra_blocks:
        return convo

    augmented_system = SystemMessage(
        content=str(convo[0].content) + "\n\n" + "\n\n".join(extra_blocks)
    )
    return [augmented_system, *convo[1:]]


def _last_human_text(convo: list[BaseMessage]) -> str:
    for msg in reversed(convo):
        if isinstance(msg, HumanMessage):
            return extract_text(msg)
    return ""


def _fire_and_forget_sync(memory: Any, role: str, content: str) -> None:
    """Schedule `memory.sync_turn(...)` without awaiting the result.

    Failures are logged at WARNING and otherwise swallowed — transcript
    persistence must never break the LLM path. When called outside a running
    event loop (e.g. test harness) we silently drop the sync.
    """
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return

    async def _runner() -> None:
        try:
            await memory.sync_turn(role, content)
        except Exception:  # noqa: BLE001 — best-effort log + swallow
            _log.warning("memory.sync_turn failed", exc_info=True)

    loop.create_task(_runner())
