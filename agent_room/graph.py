"""LangGraph wiring.

v0.2: the graph topology lives in [presets/](presets/) YAML files (or
user-supplied specs); see [docs/adr/0008-graph-spec.md](../docs/adr/0008-graph-spec.md).
The legacy `build_agent_room_graph` entry point is preserved by loading
`presets/full.yaml` internally — keeping the old API surface bit-for-bit
compatible while exercising the new spec pipeline on every test run.

v0.3: nodes with `tools: [...]` get an attached ReAct subgraph (agent ↔
ToolNode). The agent node keeps its original spec name; a sibling tool
node `<name>_tools` is added next to it, and the outbound edge gets
rewritten to a `tools_condition`-driven branch. See
[agent_room/roles/developer_react.py](roles/developer_react.py).
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import aiosqlite
from langgraph.checkpoint.memory import MemorySaver
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph
from langgraph.prebuilt import ToolNode, tools_condition

from agent_room.budget import BudgetTracker
from agent_room.config import RoleBindings
from agent_room.llm import LangChainTransport, Transport
from agent_room.obs.tracing import traced_node
from agent_room.roles import make_delivery, make_developer, make_planner, make_reviewer
from agent_room.roles.developer_react import make_developer_react
from agent_room.roles.planner_gate import make_planner_gate
from agent_room.roles.reviewer_two_call import make_reviewer_two_call
from agent_room.roles.supervisor import make_supervisor
from agent_room.roles.verifier import make_verifier
from agent_room.schemas import (
    Artifact,
    Event,
    ReviewerDecision,
    StuckDecision,
    VerificationResult,
)
from agent_room.spec import GraphSpec, NodeSpec, load_preset, resolve_router
from agent_room.state import TaskState
from agent_room.tools import Registry, default_registry, resolve_tool_names
from agent_room.tools._approval import make_approval_wrapper
from agent_room.tools.policy import DEFAULT_MODE, apply_policy

_AGENT_ROOM_MSGPACK_ALLOWLIST: tuple[type, ...] = (
    ReviewerDecision,
    Artifact,
    Event,
    VerificationResult,
    StuckDecision,
)

_ROLE_FACTORIES = {
    "planner": make_planner,
    "developer": make_developer,
    "reviewer": make_reviewer,
    "delivery": make_delivery,
    "supervisor": make_supervisor,
}

# (role, variant) → factory. variant=None falls back to `_ROLE_FACTORIES`.
_VARIANT_FACTORIES = {
    ("planner", "gate"): make_planner_gate,
    ("reviewer", "two_call"): make_reviewer_two_call,
}


def _agent_room_serde() -> JsonPlusSerializer:
    return JsonPlusSerializer(allowed_msgpack_modules=_AGENT_ROOM_MSGPACK_ALLOWLIST)


def _resolve_tools_with_policy(spec: NodeSpec, registry: Registry) -> list[Any]:
    """Resolve `spec.tools` then filter through the permission mode.

    Single source of truth so the agent factory and the ToolNode that
    actually runs the calls see the same allowed tool set. Drift here would
    mean the LLM has a tool bound that the executor refuses (or vice versa).
    """
    raw = resolve_tool_names(spec.tools, registry)
    mode = spec.tool_mode or DEFAULT_MODE
    filtered = apply_policy(raw, mode)
    if spec.tools and not filtered:
        # User listed tools but the policy refused all of them. Silent fallback
        # to a no-tool path would mask the misconfiguration; raise instead.
        names = [getattr(t, "name", "?") for t in raw]
        raise ValueError(
            f"tool_mode={mode!r} filtered out every listed tool: {names!r}. "
            f"Either pick tools the mode allows, or set tool_mode='unrestricted' "
            "if you really want write/exec access."
        )
    return filtered


def _instantiate_node(
    name: str,
    spec: NodeSpec,
    bindings: RoleBindings,
    *,
    registry: Registry,
    transport: Transport,
) -> Any:
    if spec.tools and spec.role == "developer" and spec.variant is None:
        # ReAct path. Resolution failure raises here (build time), not at runtime.
        tools = _resolve_tools_with_policy(spec, registry)
        max_rounds = spec.max_dev_rounds or 6
        return make_developer_react(
            bindings,
            tools=tools,
            max_dev_rounds=max_rounds,
            prompt_override=spec.prompt_override,
            extra_context_keys=spec.extra_context_keys,
            model=spec.model,
            transport=transport,
            serialize_tool_calls=(spec.tool_mode or DEFAULT_MODE) == "approval",
        )

    if spec.tools and spec.role != "developer":
        raise ValueError(
            f"node {name!r}: tools are only wired for role='developer' in v0.3 "
            f"(got role={spec.role!r}, tools={spec.tools!r}). "
            "Other roles are scheduled for later milestones."
        )

    if spec.role == "verifier":
        # Deterministic oracle node (goal mode) — needs the registry (for the
        # per-task workspace root) and the shell allowlist, not an LLM.
        from agent_room.config import load_settings

        settings = bindings.settings or load_settings()
        return make_verifier(registry=registry, shell_allowlist=list(settings.shell_allowlist))

    if spec.variant is None:
        factory = _ROLE_FACTORIES[spec.role]
    else:
        key = (spec.role, spec.variant)
        if key not in _VARIANT_FACTORIES:
            available = sorted(v for r, v in _VARIANT_FACTORIES if r == spec.role)
            raise ValueError(
                f"unknown variant {spec.variant!r} for role {spec.role!r} "
                f"(node {name!r}); available: {available}"
            )
        factory = _VARIANT_FACTORIES[key]
    return factory(
        bindings,
        prompt_override=spec.prompt_override,
        extra_context_keys=spec.extra_context_keys,
        model=spec.model,
        transport=transport,
    )


def _is_react_node(spec: NodeSpec) -> bool:
    return bool(spec.tools) and spec.role == "developer" and spec.variant is None


def _tool_error_to_message(exc: Exception) -> str:
    """Turn a tool execution error into a recoverable ToolMessage for the LLM.

    Without this, a rejected command (e.g. shell allowlist) or any tool raise
    propagates out of the ReAct loop and aborts the whole run. Feeding it back
    lets the developer adapt — pick an allowed command, fix the arguments —
    instead of crashing. (Found via the eval harness; see PLAN.md EVAL F1.)
    """

    return (
        f"Tool call failed: {exc}. This is a recoverable error — adjust the "
        "call (for example use an allowed command or fix the arguments) and try "
        "a different tool call."
    )


def build_uncompiled_from_spec(
    spec: GraphSpec,
    bindings: RoleBindings,
    *,
    registry: Registry | None = None,
) -> StateGraph[TaskState, Any, TaskState, TaskState]:
    """Build (uncompiled) `StateGraph` from a validated spec.

    `registry` defaults to the process-wide singleton — pass an explicit one
    when you need test isolation or per-call tool sets.
    """

    registry = registry or default_registry()
    # One transport (and therefore one BudgetTracker) shared by every role node
    # in this graph, so usage accumulates across the whole task run rather than
    # resetting per node. `bindings.budget=None` (default) makes the tracker a
    # NoOp — zero behavior change. See agent_room/budget.py.
    transport: Transport = LangChainTransport(bindings, budget=BudgetTracker(bindings.budget))

    graph: StateGraph[TaskState, Any, TaskState, TaskState] = StateGraph(TaskState)
    react_nodes: dict[str, list[Any]] = {}
    for node_name, node_spec in spec.nodes.items():
        node = _instantiate_node(
            node_name, node_spec, bindings, registry=registry, transport=transport
        )
        graph.add_node(node_name, traced_node(node_name, node))
        if _is_react_node(node_spec):
            tools = _resolve_tools_with_policy(node_spec, registry)
            tool_node_name = f"{node_name}_tools"
            tool_node_kwargs: dict[str, Any] = {"messages_key": "dev_messages"}
            if (node_spec.tool_mode or DEFAULT_MODE) == "approval":
                # Per-call human-in-the-loop gate. Halts via interrupt() and
                # resumes through service.resume(..., at_node="tool_call").
                # Approval owns its own deny/error ToolMessage synthesis, so we
                # do NOT layer handle_tool_errors on top of it.
                tool_node_kwargs["awrap_tool_call"] = make_approval_wrapper()
            else:
                # Recoverable tool errors come back to the LLM instead of
                # aborting the run (see _tool_error_to_message / PLAN EVAL F1).
                tool_node_kwargs["handle_tool_errors"] = _tool_error_to_message
            graph.add_node(
                tool_node_name,
                ToolNode(tools, **tool_node_kwargs),
            )
            react_nodes[node_name] = tools

    graph.add_edge(START, spec.entry)

    for edge in spec.edges:
        if edge.to is not None:
            target = END if edge.to == "__end__" else edge.to
            if edge.from_ in react_nodes:
                # Replace the unconditional edge with a tools-aware branch:
                # if the agent emitted tool_calls, hop to the tool node and
                # loop back; otherwise go to the original target.
                _add_react_branch(graph, edge.from_, target)
            else:
                graph.add_edge(edge.from_, target)
            continue

        # Conditional edge.
        router_fn = resolve_router(edge.router)
        mapping = {b.on: (END if b.to == "__end__" else b.to) for b in (edge.branches or [])}
        if edge.from_ in react_nodes:
            raise NotImplementedError(
                f"conditional edges out of a tool-using developer node are not "
                f"supported in v0.3 (node {edge.from_!r}). Wrap the conditional "
                "with a downstream router node instead."
            )
        graph.add_conditional_edges(edge.from_, router_fn, mapping)

    return graph


def _add_react_branch(
    graph: StateGraph[TaskState, Any, TaskState, TaskState], agent_node: str, on_end: str
) -> None:
    """Wire `agent_node` ─▶ ToolNode ─▶ agent_node loop, with `on_end` exit."""
    tool_node = f"{agent_node}_tools"

    def _route(state: TaskState) -> str:
        return tools_condition(state, messages_key="dev_messages")

    graph.add_conditional_edges(
        agent_node,
        _route,
        {"tools": tool_node, END: on_end},
    )
    graph.add_edge(tool_node, agent_node)


def build_from_spec(
    spec: GraphSpec,
    bindings: RoleBindings,
    *,
    checkpointer: Any = None,
    registry: Registry | None = None,
) -> CompiledStateGraph[TaskState, Any, TaskState, TaskState]:
    """Build & compile a graph from a validated spec."""

    graph = build_uncompiled_from_spec(spec, bindings, registry=registry)
    return graph.compile(checkpointer=checkpointer or MemorySaver())


def build_uncompiled_graph(
    bindings: RoleBindings, *, registry: Registry | None = None
) -> StateGraph[TaskState, Any, TaskState, TaskState]:
    """Build the v0.1 4-role pipeline (uncompiled).

    Backed by the `full` preset so spec-driven and legacy code paths share
    one source of truth.
    """

    return build_uncompiled_from_spec(load_preset("full"), bindings, registry=registry)


def build_agent_room_graph(
    bindings: RoleBindings,
    *,
    checkpointer: Any = None,
    registry: Registry | None = None,
) -> CompiledStateGraph[TaskState, Any, TaskState, TaskState]:
    """Build & compile the v0.1 4-role pipeline with an in-memory checkpointer.

    For production, use `build_with_sqlite_checkpointer` to get persistence.
    """

    return build_from_spec(
        load_preset("full"), bindings, checkpointer=checkpointer, registry=registry
    )


@asynccontextmanager
async def build_with_sqlite_checkpointer(
    bindings: RoleBindings,
    db_path: str,
    *,
    spec: GraphSpec | None = None,
    registry: Registry | None = None,
) -> AsyncIterator[CompiledStateGraph[TaskState, Any, TaskState, TaskState]]:
    """Async context manager that yields a compiled graph backed by SQLite.

    `spec=None` (the default) falls back to the v0.1 `full` pipeline so the
    existing CLI / server / examples are unaffected. Pass an explicit spec
    to drive a custom topology (preset name resolution lives in the CLI).
    """

    spec = spec or load_preset("full")
    async with aiosqlite.connect(db_path) as conn:
        saver = AsyncSqliteSaver(conn, serde=_agent_room_serde())
        graph = build_uncompiled_from_spec(spec, bindings, registry=registry)
        # Own the memory provider's lifecycle here — without this, a configured
        # `bindings.memory` is never initialized and silently returns nothing
        # (it's a no-op for the default NoOp provider). Curated files live next
        # to the db; the transcript layer reuses the same db (disjoint tables).
        root_dir = Path.cwd() if db_path == ":memory:" else Path(db_path).resolve().parent
        await bindings.memory.initialize(root_dir=root_dir, db_path=db_path)
        try:
            yield graph.compile(checkpointer=saver)
        finally:
            await bindings.memory.close()


GraphCompiler = Callable[
    [GraphSpec, "Registry | None"],
    CompiledStateGraph[TaskState, Any, TaskState, TaskState],
]


@asynccontextmanager
async def open_checkpointer(bindings: RoleBindings, db_path: str) -> AsyncIterator[GraphCompiler]:
    """Open the SQLite saver + initialize memory **once**, and yield a factory
    that compiles graphs sharing them.

    The server uses this to build a *per-task* graph (tools rooted under
    `workspace/<task_id>`) without re-opening the connection or re-initializing
    the shared memory provider per task. `build_with_sqlite_checkpointer` (the
    compile-once context manager) stays the path for the CLI / tests / examples.
    """
    async with aiosqlite.connect(db_path) as conn:
        saver = AsyncSqliteSaver(conn, serde=_agent_room_serde())
        root_dir = Path.cwd() if db_path == ":memory:" else Path(db_path).resolve().parent
        await bindings.memory.initialize(root_dir=root_dir, db_path=db_path)

        def compile_with(
            spec: GraphSpec, registry: Registry | None = None
        ) -> CompiledStateGraph[TaskState, Any, TaskState, TaskState]:
            return build_uncompiled_from_spec(spec, bindings, registry=registry).compile(
                checkpointer=saver
            )

        try:
            yield compile_with
        finally:
            await bindings.memory.close()


@asynccontextmanager
async def open_postgres_checkpointer(
    bindings: RoleBindings, database_url: str
) -> AsyncIterator[GraphCompiler]:
    """Production checkpointer shared by horizontally-scaled workers.

    SQLite remains the zero-infrastructure local path.  The RabbitMQ worker uses
    this PostgreSQL implementation so a redelivered or resumed task can run on
    any worker instance without a shared filesystem.
    """
    from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

    async with AsyncPostgresSaver.from_conn_string(
        database_url, serde=_agent_room_serde()
    ) as saver:
        await saver.setup()

        def compile_with(
            spec: GraphSpec, registry: Registry | None = None
        ) -> CompiledStateGraph[TaskState, Any, TaskState, TaskState]:
            return build_uncompiled_from_spec(spec, bindings, registry=registry).compile(
                checkpointer=saver
            )

        yield compile_with
