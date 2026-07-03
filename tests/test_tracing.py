"""OpenTelemetry tracing: a task run emits a task → node → llm span tree.

Uses the SDK's in-memory exporter so it runs offline. The global TracerProvider
can only be set once per process, so we set it lazily and clear the exporter
between tests.
"""

from __future__ import annotations

import pytest
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from agent_room.graph import build_agent_room_graph
from agent_room.schemas import ReviewerDecision, TaskRequest
from agent_room.service import AgentRoomService
from tests.fakes import bindings_with_fakes

_EXPORTER = InMemorySpanExporter()


def _ensure_provider() -> None:
    if not isinstance(trace.get_tracer_provider(), TracerProvider):
        provider = TracerProvider()
        provider.add_span_processor(SimpleSpanProcessor(_EXPORTER))
        trace.set_tracer_provider(provider)


def _service() -> AgentRoomService:
    bindings = bindings_with_fakes(
        decisions=[ReviewerDecision(decision="approved", feedback="ok", confidence=0.9)],
    )
    return AgentRoomService(build_agent_room_graph(bindings))


@pytest.mark.asyncio
async def test_task_run_emits_span_tree() -> None:
    _ensure_provider()
    _EXPORTER.clear()

    await _service().run(TaskRequest(title="demo", description="do it"))

    spans = _EXPORTER.get_finished_spans()
    names = [s.name for s in spans]

    assert "agent_room.task" in names
    for role in ("planner", "developer", "reviewer", "delivery"):
        assert f"node.{role}" in names, f"missing node span for {role}"
    assert any(n.startswith("llm.") for n in names), "no llm spans emitted"


@pytest.mark.asyncio
async def test_llm_span_carries_role_attribute() -> None:
    _ensure_provider()
    _EXPORTER.clear()

    await _service().run(TaskRequest(title="demo", description="do it"))

    llm_spans = [s for s in _EXPORTER.get_finished_spans() if s.name.startswith("llm.")]
    assert llm_spans
    roles = {s.attributes.get("agent_room.role") for s in llm_spans if s.attributes}
    assert "reviewer" in roles  # the reviewer's structured call is traced


@pytest.mark.asyncio
async def test_tracing_is_noop_without_provider() -> None:
    # The instrumentation itself never requires a provider — calling get_tracer
    # and spanning with the default (proxy) provider must not raise.
    from agent_room.obs.tracing import get_tracer

    with get_tracer().start_as_current_span("smoke") as span:
        span.set_attribute("agent_room.role", "planner")
