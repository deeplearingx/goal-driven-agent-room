"""ContextEngine ABC + no-op default.

Two-stage decision shape (borrowed from hermes-agent's
`agent/context_engine.py`, reimplemented here per ADR-0007):

- `should_compress(messages)` — cheap sync gate. Engines decide *whether*
  the message list needs work without paying for the work itself.
- `compress(messages)` — async because real engines call summarizer LLMs
  or read spill blobs.

`apply(messages)` is a convenience wrapper on the base class — call this
from nodes; subclasses don't need to override it.

The base class deliberately does **not** mandate a `max_tokens` /
`protect_first` / `protect_last` constructor. Those parameters belong to
specific engines (windowed truncation, summary). A future engine that
budgets by character count or by structured-message count shouldn't be
forced through a token-shaped interface.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Sequence

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, ToolMessage

from agent_room.context.tokens import count_message_tokens, count_tokens


class ContextEngine(ABC):
    """Abstract base for message-list context management.

    Implementations decide when a working-memory list (e.g.
    [`TaskState.dev_messages`](../state.py) for the developer ReAct loop)
    has grown enough to warrant compression, then return a shorter list
    that preserves task-essential context.

    Subclass contract:
    - `should_compress` MUST be cheap (no I/O, no LLM calls). It runs on
      every node entry that uses the engine.
    - `compress` MUST be idempotent on output: feeding `compress(out)`
      back in should yield `out` unchanged once `should_compress(out)`
      returns False.
    - `compress` MUST preserve message order. Callers downstream rely
      on the conversational sequence to drive ReAct / reviewer protocols.
    """

    @abstractmethod
    def should_compress(self, messages: Sequence[BaseMessage]) -> bool:
        """Return True iff `messages` exceeds the engine's budget."""

    @abstractmethod
    async def compress(self, messages: Sequence[BaseMessage]) -> list[BaseMessage]:
        """Return a compressed copy of `messages`.

        The returned list is a fresh list; callers may mutate it without
        affecting the input.
        """

    async def apply(self, messages: Sequence[BaseMessage]) -> list[BaseMessage]:
        """Run `should_compress` → `compress` and return the resulting list.

        Always returns a fresh list (a copy when no compression happened),
        so callers can safely append without worrying about whether they
        own the buffer.
        """
        if not self.should_compress(messages):
            return list(messages)
        return await self.compress(messages)


class NoOpContextEngine(ContextEngine):
    """Default engine: never compresses, always passes through.

    Used as the safe default in `RoleBindings` until a real engine is
    wired in (v0.4 §4.5). Behaviour is identical to having no engine
    at all, but keeps the call-site shape uniform so §4.5 can switch
    without touching every node.
    """

    def should_compress(self, messages: Sequence[BaseMessage]) -> bool:
        return False

    async def compress(self, messages: Sequence[BaseMessage]) -> list[BaseMessage]:
        return list(messages)


_TRUNCATED_TEMPLATE = "[context truncated: {n} messages omitted]"


class WindowedContextEngine(ContextEngine):
    """Message-count budget with head + tail protection windows.

    ⚠️ **Real-load warning (2026-06-23):** dropping the middle destroys the
    developer's working memory. A real-LLM ablation on a multi-file ReAct task
    showed this engine made the agent *churn* (re-exploring what it had already
    seen) to ~2x the rounds and **fail**, while NoOp passed and
    `SummaryContextEngine` — which summarises the middle instead of dropping it —
    passed. **Prefer `SummaryContextEngine` for ReAct loops.** This engine is
    only safe when the dropped span is genuinely disposable (not a working set).
    See [docs/context.md](../../docs/context.md) "Real-load finding".

    Borrowed shape from hermes-agent's `agent/context_engine.py`
    (`protect_first_n=3, protect_last_n=6`), reimplemented here per
    ADR-0007. When `len(messages) > max_messages`, drops the *middle*
    span and replaces it with a single marker `HumanMessage` so the LLM
    sees that context was cut rather than just a missing turn.

    Budget unit is **message count**, not tokens. A token-aware engine
    would need a tokenizer dependency; that responsibility is deferred
    to §4.3 (`SummaryContextEngine`) or a later v0.4.x extension. For
    the developer ReAct loop the message-count proxy is good enough:
    each round adds an AI + ToolMessage pair, so a budget of e.g. 30
    cleanly maps to ~15 ReAct rounds with the most recent few protected.

    Construction invariants (raise `ValueError`):
    - `protect_first_n >= 0`, `protect_last_n >= 0`
    - `max_messages >= protect_first_n + protect_last_n + 1` — the
      compression result is always
      `protect_first + 1 (marker) + protect_last` long; for the next
      `should_compress(result)` to return False (idempotency, contract
      item 2), `max_messages` must be at least that.

    Why HumanMessage for the marker (not SystemMessage):
    - Some providers merge / reject multiple System messages, or only
      accept SystemMessage at index 0.
    - The marker is conceptually part of the user-side stream telling
      the model "context here was elided", not a new system instruction.

    ReAct boundary alignment:
    Naive head/tail slicing on a ReAct conversation can produce illegal
    sequences — e.g. a tail starting with a `ToolMessage` whose matching
    `AIMessage(tool_calls=...)` was just dropped. Anthropic's Messages
    API rejects this. After slicing, the engine drops orphan leading
    `ToolMessage`s from the tail until either the tail is empty or its
    first message has no `tool_call_id`. This may shrink the tail below
    `protect_last_n`; that's the right tradeoff — losing one extra
    ToolMessage is far cheaper than producing a request the provider
    refuses.
    """

    def __init__(
        self,
        *,
        max_messages: int | None = None,
        max_tokens: int | None = None,
        protect_first_n: int = 0,
        protect_last_n: int = 6,
        target_ratio: float = 0.5,
    ) -> None:
        if (max_messages is None) == (max_tokens is None):
            raise ValueError("set exactly one of max_messages or max_tokens")
        if protect_first_n < 0:
            raise ValueError(f"protect_first_n must be >= 0, got {protect_first_n}")
        if protect_last_n < 0:
            raise ValueError(f"protect_last_n must be >= 0, got {protect_last_n}")
        # Message-count mode: +1 for the marker message inserted between the bands.
        min_budget = protect_first_n + protect_last_n + 1
        if max_messages is not None and max_messages < min_budget:
            raise ValueError(
                f"max_messages ({max_messages}) must be >= "
                f"protect_first_n + protect_last_n + 1 ({min_budget}); "
                "otherwise the compressed result would itself trigger "
                "another compression (idempotency violation)."
            )
        # Token mode: the post-compression tail is built to fit a fraction of the
        # budget, so the result lands well under `max_tokens` and won't re-fire.
        if max_tokens is not None and not 0.0 < target_ratio <= 1.0:
            raise ValueError(f"target_ratio must be in (0, 1], got {target_ratio}")
        self.max_messages = max_messages
        self.max_tokens = max_tokens
        self.protect_first_n = protect_first_n
        self.protect_last_n = protect_last_n
        self.target_ratio = target_ratio

    def should_compress(self, messages: Sequence[BaseMessage]) -> bool:
        if self.max_tokens is not None:
            return count_tokens(messages) > self.max_tokens
        assert self.max_messages is not None  # guaranteed by __init__
        return len(messages) > self.max_messages

    async def compress(self, messages: Sequence[BaseMessage]) -> list[BaseMessage]:
        if not self.should_compress(messages):
            return list(messages)
        head, middle, tail = self._partition(messages)
        if not middle:
            # Nothing between the protected bands — compressing would only add a
            # marker without shrinking. Leave as-is to guarantee progress.
            return list(messages)
        marker = await self._build_marker(middle)
        return head + [marker] + tail

    def _partition(
        self, messages: Sequence[BaseMessage]
    ) -> tuple[list[BaseMessage], list[BaseMessage], list[BaseMessage]]:
        """Split messages into (head, middle, tail) with ReAct alignment.

        Naive `messages[:N]` / `messages[-M:]` slicing can produce illegal
        provider sequences. We trim:
        - dangling `AIMessage(tool_calls=...)` off the head's tail end —
          its answering `ToolMessage` would land in middle and be dropped;
        - orphan `ToolMessage`s off the tail's front — their parent
          `AIMessage(tool_calls=...)` would land in middle and be dropped.
        Whatever the trim removed becomes part of `middle`, which is what
        subclasses (e.g. `SummaryContextEngine`) summarize.
        """
        if self.max_tokens is not None:
            return self._partition_by_tokens(messages)
        head = list(messages[: self.protect_first_n])
        tail = list(messages[-self.protect_last_n :]) if self.protect_last_n > 0 else []
        while head and _is_ai_with_tool_calls(head[-1]):
            head.pop()
        while tail and isinstance(tail[0], ToolMessage):
            tail.pop(0)
        middle = list(messages[len(head) : len(messages) - len(tail)])
        return head, middle, tail

    def _partition_by_tokens(
        self, messages: Sequence[BaseMessage]
    ) -> tuple[list[BaseMessage], list[BaseMessage], list[BaseMessage]]:
        """Token-budget partition: `protect_first_n` head messages + a tail built
        from the most recent messages until a token budget is hit.

        The tail budget is `target_ratio` of what's left after the head, so the
        result (head + marker + tail) lands well under `max_tokens` and the
        compression is idempotent. Same ReAct alignment as the count-based path.
        """
        assert self.max_tokens is not None
        head = list(messages[: self.protect_first_n])
        while head and _is_ai_with_tool_calls(head[-1]):
            head.pop()

        tail_budget = max(int((self.max_tokens - count_tokens(head)) * self.target_ratio), 0)
        tail: list[BaseMessage] = []
        used = 0
        for msg in reversed(messages[len(head) :]):
            cost = count_message_tokens(msg)
            if tail and used + cost > tail_budget:
                break
            tail.insert(0, msg)
            used += cost
        while tail and isinstance(tail[0], ToolMessage):
            tail.pop(0)

        middle = list(messages[len(head) : len(messages) - len(tail)])
        return head, middle, tail

    async def _build_marker(self, middle: Sequence[BaseMessage]) -> BaseMessage:
        """Produce the single message that replaces the dropped middle span.

        Default implementation: a fixed-template `HumanMessage` with the
        omitted count. `SummaryContextEngine` overrides this to call a
        summarizer LLM. The hook is async because real summarizers do I/O;
        the default returns immediately.
        """
        return HumanMessage(content=_TRUNCATED_TEMPLATE.format(n=len(middle)))


def _is_ai_with_tool_calls(msg: BaseMessage) -> bool:
    return isinstance(msg, AIMessage) and bool(getattr(msg, "tool_calls", None))
