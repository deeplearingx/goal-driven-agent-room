"""Context engineering — agent-room v0.4.

Borrowed from hermes-agent's two-stage `should_compress` + `compress` shape,
reimplemented here per ADR-0007. Engines:

- `NoOpContextEngine` (§4.1) — safe default, never compresses.
- `WindowedContextEngine` (§4.2) — head/tail message-count budget with
  middle-span truncation marker.
- `SummaryContextEngine` (§4.3) — subclass of Windowed that replaces the
  count-only marker with an LLM-generated summary of the dropped span.
"""

from __future__ import annotations

from agent_room.context.engine import (
    ContextEngine,
    NoOpContextEngine,
    WindowedContextEngine,
)
from agent_room.context.summary import SummaryContextEngine

__all__ = [
    "ContextEngine",
    "NoOpContextEngine",
    "SummaryContextEngine",
    "WindowedContextEngine",
]
