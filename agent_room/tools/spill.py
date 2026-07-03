"""Large tool-output spill — v0.4 §3.4 / RISK-9 / ADR-0009.

Big ToolMessage payloads (multi-KB shell stdout, repo-wide globs, file reads
on large files) wreck two budgets simultaneously:

1. The developer's prompt budget — even after `WindowedContextEngine`
   protects the tail, a single 50KB shell stdout in the protected slot
   can blow past the provider's context window in one turn.
2. The summarizer's prompt budget — when `SummaryContextEngine` serializes
   the dropped middle for the auxiliary LLM, a 50KB ToolMessage there
   spends real money to "summarize" a stack trace the model will then
   summarize as "the build failed."

Spill replaces the body of an oversized `ToolMessage` with a stable
content-hashed reference + a short preview. The full bytes go to a store
(in-process by default; subclassable for disk / SQLite when v0.5 lands
its persistence layer). The placeholder shape is provider-legal — still
a `ToolMessage` with the same `tool_call_id` — so the LangChain message
list stays valid through ToolNode → engine → ainvoke.

This module deliberately stays small. It does NOT:

- run a 3-layer turn-aggregate enforcer (hermes' `enforce_turn_budget`).
  Spilling per-message is enough for the cases we hit; the aggregate
  layer can be added when we see real workloads that need it.
- coordinate with `read_text` / `read_file` for restoring spilled output
  back into context. Restoration is a v0.5 concern — by then we'll have
  a persistent store and a `ref://` resolver.
- do any I/O by default. The shipped `InMemorySpillStore` is process-local;
  `SpillStore.put` is sync because the in-memory implementation has no
  reason to be async. Subclasses that hit disk can override.

Borrowed in spirit from hermes-agent's `tools/tool_result_storage.py`
(`maybe_persist_tool_result` + content-hashed naming) but reimplemented
per ADR-0007 with no sandbox-execute coupling, no env handle, no
`<persisted-output>` xml tags. The placeholder is plain text the model
can read at a glance.
"""

from __future__ import annotations

import hashlib
import logging
import threading
from collections.abc import Iterable
from dataclasses import dataclass

from langchain_core.messages import BaseMessage, ToolMessage

logger = logging.getLogger(__name__)

# Default threshold — payloads at or below this size are not spilled.
# 4 KB roughly matches "one screenful of stdout"; anything larger usually
# isn't load-bearing for the next decision.
DEFAULT_SPILL_THRESHOLD = 4_000

# Preview cap inside the placeholder. Big enough to keep first-line errors
# visible (`pytest` failure summary, shell error), small enough that the
# placeholder itself never exceeds ~600 chars.
_PREVIEW_CHARS = 400

_REF_PREFIX = "spill:"
_REF_HASH_LEN = 12  # short prefix of sha256 — collision-resistant in practice


@dataclass(frozen=True)
class SpillRef:
    """Opaque pointer to a spilled payload.

    `ref` is the content-hashed identifier. Two messages with byte-identical
    bodies share a ref — `put` is content-addressable and idempotent.
    """

    ref: str
    size: int

    def placeholder_text(self, preview: str) -> str:
        return _format_placeholder(self.ref, self.size, preview)


class SpillStore:
    """Abstract spill backend.

    Implementations override `put` / `get`. The default
    `InMemorySpillStore` is fine for tests, single-process runs, and any
    case where payloads don't need to outlive the process. Disk-backed
    or SQLite-backed stores can subclass without touching the engine.
    """

    def put(self, payload: str) -> SpillRef:
        raise NotImplementedError

    def get(self, ref: str) -> str | None:
        raise NotImplementedError


class InMemorySpillStore(SpillStore):
    """Process-local content-hashed store. Thread-safe, no I/O."""

    def __init__(self) -> None:
        self._data: dict[str, str] = {}
        self._lock = threading.Lock()

    def put(self, payload: str) -> SpillRef:
        digest = hashlib.sha256(payload.encode("utf-8", errors="replace")).hexdigest()
        ref = digest[:_REF_HASH_LEN]
        with self._lock:
            # Idempotent on identical content; first writer wins, later
            # writers no-op. Different contents that collide on the short
            # ref are impossibly rare (12 hex chars = 48 bits) but if it
            # ever happens the second writer would silently overwrite —
            # log it instead.
            existing = self._data.get(ref)
            if existing is None:
                self._data[ref] = payload
            elif existing != payload:
                logger.warning(
                    "spill ref %r collision: existing %d chars, new %d chars; keeping existing",
                    ref,
                    len(existing),
                    len(payload),
                )
        return SpillRef(ref=ref, size=len(payload))

    def get(self, ref: str) -> str | None:
        with self._lock:
            return self._data.get(ref)

    def __len__(self) -> int:
        with self._lock:
            return len(self._data)


def maybe_spill_tool_message(
    msg: ToolMessage,
    store: SpillStore,
    *,
    threshold: int = DEFAULT_SPILL_THRESHOLD,
) -> ToolMessage:
    """Return a spill-replaced `ToolMessage` if oversized, else `msg` unchanged.

    Idempotent: a `ToolMessage` whose body is already a spill placeholder
    passes through unchanged (we detect the `[spill:` prefix). Callers
    can run this on the same message twice without worry.

    The returned message preserves `tool_call_id`, `name`, and any other
    metadata; only `content` changes. The original is never mutated.
    """
    body = _stringify(msg.content)
    if len(body) <= threshold:
        return msg
    if _looks_like_placeholder(body):
        return msg

    ref = store.put(body)
    preview = _make_preview(body)
    placeholder = ref.placeholder_text(preview)
    # ToolMessage takes positional content + tool_call_id keyword.
    return ToolMessage(
        content=placeholder,
        tool_call_id=msg.tool_call_id,
        name=getattr(msg, "name", None),
    )


def spill_messages(
    messages: Iterable[BaseMessage],
    store: SpillStore,
    *,
    threshold: int = DEFAULT_SPILL_THRESHOLD,
) -> list[BaseMessage]:
    """Run `maybe_spill_tool_message` over every `ToolMessage` in a list.

    Non-tool messages pass through unchanged. Returns a fresh list; never
    mutates the input. This is the helper `SummaryContextEngine` uses
    when `spill_store` is wired in — every middle ToolMessage gets the
    chance to spill before serialization, so summarizer prompts stay
    bounded by `threshold * len(middle)` rather than by raw payload sizes.
    """
    out: list[BaseMessage] = []
    for m in messages:
        if isinstance(m, ToolMessage):
            out.append(maybe_spill_tool_message(m, store, threshold=threshold))
        else:
            out.append(m)
    return out


def _format_placeholder(ref: str, size: int, preview: str) -> str:
    return (
        f"[{_REF_PREFIX}{ref} size={size}] "
        f"output spilled to store; preview ({len(preview)}/{size} chars):\n{preview}"
    )


def _looks_like_placeholder(body: str) -> bool:
    return body.startswith(f"[{_REF_PREFIX}")


def _make_preview(body: str) -> str:
    if len(body) <= _PREVIEW_CHARS:
        return body
    head = body[:_PREVIEW_CHARS]
    last_newline = head.rfind("\n")
    if last_newline > _PREVIEW_CHARS // 2:
        head = head[: last_newline + 1]
    return head


def _stringify(content: object) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if isinstance(block, dict):
                txt = block.get("text") or block.get("content")
                parts.append(str(txt) if txt is not None else str(block))
            else:
                parts.append(str(block))
        return "\n".join(parts)
    return str(content)
