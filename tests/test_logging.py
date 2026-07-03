"""Structured logging: a task run emits task.start / task.complete with task_id,
and the OTEL processor adds trace ids only when a span is active.
"""

from __future__ import annotations

import logging

import pytest
from structlog.testing import capture_logs

from agent_room.graph import build_agent_room_graph
from agent_room.obs.logging import _add_otel_trace, configure_logging
from agent_room.schemas import ReviewerDecision, TaskRequest
from agent_room.service import AgentRoomService
from tests.fakes import bindings_with_fakes


@pytest.mark.asyncio
async def test_task_run_emits_structured_lifecycle_logs() -> None:
    # Lifecycle logs are INFO; the quiet default (WARNING) filters them out, so
    # opt into INFO for the test (and restore the default afterwards).
    configure_logging(level=logging.INFO)
    try:
        bindings = bindings_with_fakes(
            decisions=[ReviewerDecision(decision="approved", feedback="ok", confidence=0.9)],
        )
        service = AgentRoomService(build_agent_room_graph(bindings))

        with capture_logs() as logs:
            result = await service.run(TaskRequest(title="demo", description="do it"))

        events = {e["event"] for e in logs}
        assert "task.start" in events
        assert "task.complete" in events

        complete = next(e for e in logs if e["event"] == "task.complete")
        assert complete["task_id"]
        assert complete["status"] == result.status
    finally:
        configure_logging(level=logging.WARNING)


def test_otel_processor_noop_without_active_span() -> None:
    out = _add_otel_trace(None, "info", {"event": "x"})  # type: ignore[arg-type]
    assert "trace_id" not in out and "span_id" not in out
