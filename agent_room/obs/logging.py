"""Structured logging (structlog), correlated with the OpenTelemetry trace.

Operational logs — distinct from the user-facing `Event` stream (SSE) and from
spans (OTEL). Each record is a dict with stable fields (`task_id`, `role`,
`event`, …) and, when a span is active, the `trace_id` / `span_id` so a log line
points straight at its place in the trace.

Quiet by default: configured at import to WARNING with a console renderer, so a
library consumer sees only warnings. Opt into verbose / JSON via
`configure_logging()` or `AGENT_ROOM_LOG=info` (+ `AGENT_ROOM_LOG_JSON=1`).
"""

from __future__ import annotations

import logging
import os
from typing import TYPE_CHECKING, Any

import structlog
from opentelemetry import trace

if TYPE_CHECKING:
    from structlog.typing import EventDict, WrappedLogger


def _add_otel_trace(_logger: WrappedLogger, _method: str, event_dict: EventDict) -> EventDict:
    span = trace.get_current_span()
    ctx = span.get_span_context()
    if ctx.is_valid:
        event_dict["trace_id"] = format(ctx.trace_id, "032x")
        event_dict["span_id"] = format(ctx.span_id, "016x")
    return event_dict


_PROCESSORS: list[Any] = [
    structlog.contextvars.merge_contextvars,
    structlog.processors.add_log_level,
    structlog.processors.TimeStamper(fmt="iso"),
    _add_otel_trace,
]


def _configure(*, json: bool, level: int) -> None:
    renderer = structlog.processors.JSONRenderer() if json else structlog.dev.ConsoleRenderer()
    structlog.configure(
        processors=[*_PROCESSORS, renderer],
        wrapper_class=structlog.make_filtering_bound_logger(level),
        cache_logger_on_first_use=False,
    )


# Quiet, structured, trace-correlated default. The host can override.
_configure(json=False, level=logging.WARNING)


def get_logger(name: str = "agent_room") -> Any:
    return structlog.get_logger(name)


def configure_logging(*, json: bool = False, level: int = logging.INFO) -> None:
    """Opt into verbose / JSON structured logs."""

    _configure(json=json, level=level)


def configure_from_env() -> None:
    """Configure from `AGENT_ROOM_LOG` (debug/info/warning/error) + `AGENT_ROOM_LOG_JSON`."""

    name = os.getenv("AGENT_ROOM_LOG", "").strip().upper()
    if name in ("DEBUG", "INFO", "WARNING", "ERROR"):
        configure_logging(
            json=os.getenv("AGENT_ROOM_LOG_JSON", "") == "1",
            level=getattr(logging, name),
        )


def bind_task(task_id: str) -> None:
    """Bind `task_id` into the context so every log in this task carries it."""

    structlog.contextvars.bind_contextvars(task_id=task_id)
