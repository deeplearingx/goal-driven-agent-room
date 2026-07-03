"""OpenTelemetry tracing for agent runs.

Zero-impact by default: instrumentation calls `get_tracer()`, which returns a
no-op tracer until a `TracerProvider` is configured. Nothing is exported unless
the caller opts in (`configure_console_tracing()`, `configure_from_env()`, or
their own provider) — so production and tests pay nothing for spans they don't
collect, and no external service is ever required.

LLM spans use the OpenTelemetry **GenAI semantic conventions** (`gen_ai.*`) so
the traces drop into standard GenAI observability backends, plus an
`agent_room.*` namespace for our own attributes (role, node, task id).

Span tree of one task:

    agent_room.task
    ├── node.planner   └── llm.invoke      (gen_ai.usage.*)
    ├── node.developer └── llm.invoke ...
    ├── node.reviewer  └── llm.structured
    └── node.delivery
"""

from __future__ import annotations

import os
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Any, TypeVar

from opentelemetry import trace

if TYPE_CHECKING:
    from opentelemetry.trace import Tracer

_TRACER_NAME = "agent_room"

# OpenTelemetry GenAI semantic-convention keys (the subset we set).
GEN_AI_SYSTEM = "gen_ai.system"
GEN_AI_OPERATION = "gen_ai.operation.name"
GEN_AI_REQUEST_MODEL = "gen_ai.request.model"
GEN_AI_USAGE_INPUT = "gen_ai.usage.input_tokens"
GEN_AI_USAGE_OUTPUT = "gen_ai.usage.output_tokens"
# agent_room namespace.
ROLE = "agent_room.role"
NODE = "agent_room.node"
TASK_ID = "agent_room.task_id"


def get_tracer() -> Tracer:
    """The agent-room tracer. A no-op until a `TracerProvider` is configured."""

    return trace.get_tracer(_TRACER_NAME)


def configure_console_tracing() -> None:
    """Opt-in: print spans to stdout. Local debugging, no external service."""

    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import ConsoleSpanExporter, SimpleSpanProcessor

    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(ConsoleSpanExporter()))
    trace.set_tracer_provider(provider)


def configure_from_env() -> None:
    """Configure tracing from `AGENT_ROOM_TRACE` (`console` enables stdout).

    Anything else (including unset) leaves the no-op default in place. Safe to
    call once at startup (CLI / server); a no-op when tracing isn't requested.
    """

    if os.getenv("AGENT_ROOM_TRACE", "").strip().lower() == "console":
        configure_console_tracing()


_T = TypeVar("_T")


def traced_node(name: str, fn: Callable[[Any], Awaitable[_T]]) -> Callable[[Any], Awaitable[_T]]:
    """Wrap a graph node callable so each invocation opens a `node.<name>` span."""

    async def wrapped(state: Any) -> _T:
        with get_tracer().start_as_current_span(f"node.{name}") as span:
            span.set_attribute(NODE, name)
            return await fn(state)

    return wrapped
