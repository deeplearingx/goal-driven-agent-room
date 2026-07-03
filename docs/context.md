# Context engineering — v0.4 system

This document is the developer-facing reference for `agent_room.context` —
the ABC, the three concrete engines, the load-bearing invariants, and how
to choose between them.

> Design rationale:
> - Independence: [ADR-0007](adr/0007-borrow-not-integrate-hermes.md) — the
>   "compress middle, protect head + tail" pattern is borrowed from
>   hermes-agent, but everything is reimplemented in this repo. Zero
>   `from hermes` imports.
> - Default behaviour is unchanged: `RoleBindings.context_engine` defaults
>   to `NoOpContextEngine`, so v0.1-v0.3 graphs see the same wire format
>   they always have.
> - Compress prompt, not state. The engine bounds the messages handed to
>   the LLM each turn; `state["dev_messages"]` keeps growing under its
>   `add` reducer so the audit trail stays intact.

## Why this exists

A ReAct developer loop calls tools many times. Without compression, every
round resends the entire growing conversation, so:

1. Token usage scales `O(rounds²)` — round 30 sends round 0's context plus
   29 more turns of tool calls and results.
2. Long tool outputs (file reads, shell stdout) push out the planner's
   instructions and the reviewer's most recent feedback. The model
   "forgets" what it was supposed to do.
3. Hard cost ceilings (per-task token budget, provider rate limits) trip
   silently. Failures look like timeouts or refusals, not budget
   exhaustion.

Context engineering is the layer that turns "the conversation" (whatever
ReAct emits) into "the prompt" (a bounded message list). Same role messages
get the same opportunity to think, but the prompt is shaped before each
LLM call.

## The contract

`agent_room/context/engine.py` defines the ABC:

```python
class ContextEngine(ABC):
    def should_compress(self, messages: Sequence[BaseMessage]) -> bool: ...
    async def compress(self, messages: Sequence[BaseMessage]) -> list[BaseMessage]: ...

    async def apply(self, messages: Sequence[BaseMessage]) -> list[BaseMessage]:
        """Convenience: gate-then-compress; identity if under budget."""
        if not self.should_compress(messages):
            return list(messages)
        return await self.compress(messages)
```

Two invariants every implementation must hold:

- **Idempotent on already-bounded input**: `apply(apply(msgs)) == apply(msgs)`.
  Re-running the engine on its own output must not produce a different
  list. This lets callers retry without fear, and lets us reason about
  fixpoints in failure recovery paths.
- **No mutation of the input list**: callers (the developer ReAct node)
  pass `state["dev_messages"]` directly. Mutating it would corrupt the
  LangGraph reducer state. Implementations must always return a fresh list.

## The three engines

### `NoOpContextEngine`

Identity. `should_compress` is always `False`, `compress` is never called,
`apply` returns `list(messages)` so even the no-op path defends the
no-mutation invariant.

This is the default in `RoleBindings.context_engine`. Existing graphs that
don't opt in see no behaviour change.

### `WindowedContextEngine(max_messages, protect_first_n, protect_last_n)`

Drops the middle of the conversation when length exceeds `max_messages`.
Replaces the dropped span with a single placeholder:

```text
HumanMessage("[context truncated: N messages omitted]")
```

Construction-time hard invariants (these raise `ValueError`, not log a
warning, because passing impossible budgets means the caller has a bug
in their config):

- `max_messages >= 1`
- `protect_first_n >= 0`, `protect_last_n >= 0`
- `max_messages >= protect_first_n + protect_last_n + 1`

The "+ 1" is the marker. After compression the result is exactly
`protect_first_n + 1 + protect_last_n` messages long — always — so
`apply` is naturally idempotent.

**ReAct boundary alignment** is the load-bearing detail. A naive
head/tail slice can split a tool-call/tool-result pair: the head ends with
`AIMessage(tool_calls=[...])`, the marker drops the matching `ToolMessage`,
and the tail starts with another `ToolMessage` whose `tool_call_id` no
longer maps to anything in scope. Anthropic and OpenAI both reject this
shape with a 400.

The engine fixes this by walking the tail forward until the first message
is either a `HumanMessage`, a `SystemMessage`, or an `AIMessage` (no
orphan `ToolMessage` first). It also walks the head backward to drop a
trailing `AIMessage(tool_calls=...)` whose match was discarded. The
result is always a legal message list.

### `SummaryContextEngine(summarizer, max_messages, protect_first_n, protect_last_n, preamble?)`

Subclass of `WindowedContextEngine`. Same gate, same head/tail slicing,
same boundary alignment — only the marker generation changes. Instead of
the count-only placeholder, it serializes the dropped span and asks a
small LLM to summarize it.

The marker shape:

```text
[context summary — earlier turns compacted]
<summary body the LLM produced>
[summary represents N omitted messages]
```

Three failure paths fall back to the parent's count-only marker (with a
warning log) so a single summary failure can't take down a long ReAct
loop:

1. `summarizer.ainvoke(...)` raises (network error, provider 5xx, etc.).
2. Summarizer returns whitespace-only or empty string.
3. The middle is empty (no messages to summarize). Engine doesn't even
   call the LLM in this case.

The serialization helper tags every dropped message with its role and
`tool_call_id` (when applicable), and truncates each at 1500 chars. When
a `spill_store` is wired in (see below), large `ToolMessage` bodies are
replaced by `[spill: ref=<id>] preview...` placeholders **before**
serialization, so the summarizer's prompt is bounded by the spill
threshold rather than by raw payload size. The 1500-char cap is the
floor — protection against an unspilled message that crept through.

**What this engine is not**: hermes-agent's iterative summary chain
(each compression takes the previous summary as context and refines it).
ReAct loops in this repo are typically 6-15 rounds; multiple compressions
in one task are rare. We skip the chained-summary complexity and pay the
cost of "summarize from scratch" each time. If you find yourself running
50+ round tasks routinely, that's the upgrade path.

### Spill — large `ToolMessage` payloads

`agent_room/tools/spill.py` is a tiny, optional sidecar that turns
"a 30 KB shell stdout in the middle" into "a 200-char placeholder + a
content-hashed pointer". It plugs into `SummaryContextEngine` via the
`spill_store=` constructor kwarg and only fires for `ToolMessage`s
above a configurable size threshold (default 4 KB).

```python
from agent_room.tools.spill import InMemorySpillStore
from agent_room.context import SummaryContextEngine

store = InMemorySpillStore()
engine = SummaryContextEngine(
    summarizer=small_llm,
    max_messages=20,
    protect_first_n=2,
    protect_last_n=8,
    spill_store=store,
    spill_threshold=4_000,
)
```

When the engine compacts the dropped middle, every oversized
`ToolMessage` is rewritten to:

```text
[spill:<sha256-12char> size=NNNNN] output spilled to store; preview (P/N chars):
<first ~400 chars of body, snapped at last newline>
```

The full body lands in the store keyed by content hash; identical
payloads dedupe automatically. The summarizer never sees the raw bytes.
The developer LLM never sees the spill placeholder either — spill only
operates on the **summarizer's** prompt; the engine's output (head + 1
marker + tail) handed back to the developer is unchanged.

Why this lives next to context engineering rather than alongside the
tool runtime: spill exists *because* of summarizer prompt budgets. The
developer LLM happily accepts a 30 KB ToolMessage in its protected tail
(provider context windows are ~200 KB); only the auxiliary summarizer
gets squeezed when that body lands in its prompt as middle-span text.
Spill is therefore a context-engine concern, not a tool concern.

What spill is NOT (deliberately):

- **Not a 3-layer turn-aggregate budget** like hermes' `enforce_turn_budget`.
  Per-`ToolMessage` thresholding has been enough so far.
- **Not coordinated with `read_text` / `read_file`**. Restoring spilled
  output back into context is a v0.5 concern, when we'll have a
  persistent store and a `ref://` resolver.
- **Not enabled by default**. `RoleBindings.context_engine` defaults to
  `NoOpContextEngine`; even when callers swap in `SummaryContextEngine`,
  `spill_store` defaults to `None` to keep behaviour identical to v0.4
  §4.3 unless the caller opts in.

## Choosing an engine

| Engine | When | Cost per turn |
|---|---|---|
| `NoOpContextEngine` (default) | Tasks ≤ 6 ReAct rounds, predictable token budget | 0 |
| `WindowedContextEngine` | Long tasks, lossy compression OK, want zero LLM overhead | 0 |
| `SummaryContextEngine` | Long tasks, need the model to remember earlier decisions | 1 LLM call when gate fires |

Rule of thumb: start with `NoOpContextEngine`. If a task hits the
provider's context limit or the bill jumps past expectations, swap in
`WindowedContextEngine(max_messages=20, protect_first_n=2, protect_last_n=8)`
and re-run. If the developer starts repeating itself ("I should read
auth.py" three times because each compression dropped the prior read
result), upgrade to `SummaryContextEngine`.

## Integration point

`agent_room/roles/developer_react.py` owns the only `apply()` call site:

```python
convo = existing or prompt_builder.build(state)
# ... budget-note logic ...
llm_input = await context_engine.apply(convo)
response = await llm.bind_tools(tools).ainvoke(llm_input)
```

Two things to note:

- **The persisted state is not touched.** `state["dev_messages"]` keeps
  growing every turn under its `add` reducer. The engine bounds only the
  list handed to `llm.ainvoke(...)`. Audit replay, checkpoint resume, and
  reviewer feedback always see the full trail.
- **`_append_budget_note` runs before compression.** When the dev round
  budget is exhausted we patch the trailing `HumanMessage` with a "force
  convergence" note. That note rides through the protected tail, so the
  LLM sees it on the very call where we drop `bind_tools`.

## Testing invariants pinned in this repo

The full set of guarantees ([`tests/test_context_engine.py`](../tests/test_context_engine.py),
[`test_context_windowed.py`](../tests/test_context_windowed.py),
[`test_context_summary.py`](../tests/test_context_summary.py),
[`test_context_react_integration.py`](../tests/test_context_react_integration.py),
[`test_context_long_task.py`](../tests/test_context_long_task.py),
[`test_spill.py`](../tests/test_spill.py),
[`test_context_summary_spill.py`](../tests/test_context_summary_spill.py)):

- `apply` is idempotent.
- Construction rejects impossible budgets.
- ReAct boundary alignment never produces an orphan `ToolMessage`.
- Compression is gated; under-budget input never calls the LLM (relevant
  for `SummaryContextEngine` cost).
- `state["dev_messages"]` grows monotonically across 50 rounds; LLM input
  stays bounded across 50 rounds; head messages are byte-equal across all
  50 compressions.
- Summarizer failure falls back to count marker; the loop continues.
- Spill store is content-addressable + idempotent; placeholder shape is
  itself idempotent (re-spilling a placeholder is a no-op); spill never
  changes the engine's output marker shape (head + 1 marker + tail).

## Related work

- [ADR-0007](adr/0007-borrow-not-integrate-hermes.md) — independence
  policy. Why we reimplement instead of importing.
- §3.4 spill ([`agent_room/tools/spill.py`](../agent_room/tools/spill.py)):
  big tool outputs land in a content-hashed store with a placeholder
  reference. `SummaryContextEngine` sees the placeholder, not the raw
  content. Tracked in [PLAN.md](../PLAN.md) §8 row 11.

## Example

[`examples/context_compression.py`](../examples/context_compression.py)
runs the same long task through all three engines side-by-side. It uses
synthetic LLMs so it runs offline (no provider creds needed) and prints
the per-turn message counts so you can watch compression engage.

## Real-load finding (2026-06-23) — drop is harmful, summarise

The offline tests above prove each engine is *mechanically* correct (legal
message sequences, idempotent compression). They do **not** show how an engine
affects a real agent's behaviour. A real-LLM ablation (DeepSeek-V4-Pro) on a
multi-file ReAct bug-fix task — same task, same window params, only the engine
differs — found:

| engine | trigger | result | ReAct msgs | tokens | wall |
|--------|---------|--------|-----------|--------|------|
| `NoOpContextEngine` | — | ✅ pass | 15 | 12K | 47s |
| `WindowedContextEngine` (drop) | message-count | ❌ **fail** | 37 (churn) | 17K | 86s |
| `SummaryContextEngine` (summarise) | message-count | ✅ pass | 34 | **40K** | 110s |
| `SummaryContextEngine` (summarise) | **token** (`max_tokens`) | ✅ pass | **22** | **21K** | **49s** |

**Dropping the middle destroys the developer's working memory.** It forgets
what it already read/tried, re-explores in circles, and burns *more* tokens
while still failing. Windowed's failure reproduced three times (38 / 35 / 37
messages). **Summarising the middle preserves enough memory to converge** — the
borrow-from-hermes lesson: compress by *summarising*, not discarding.

Two caveats this surfaced — both now addressed by the **token-pressure trigger**:

1. **Compression is not free.** Summarising on a *message-count* trigger
   over-fires (it compresses every time the list passes `max_messages=8`, calling
   the summariser repeatedly) — the most expensive variant at 40K tokens / 110s.
   The **token trigger** only compresses under real token pressure, halving the
   cost (21K / 49s) while staying correct. So: summarise, *and* trigger on tokens.
2. **Trigger on token pressure, not message count.** `max_messages=8` fired on a
   task that fit fine in 21 messages, adding pure overhead. **Now implemented:**
   pass `max_tokens=` instead of `max_messages=` and the engine triggers on token
   count (via [`agent_room/context/tokens.py`](../agent_room/context/tokens.py),
   tiktoken with a chars/4 fallback) — a small-but-chatty conversation never
   compresses. In token mode the tail is rebuilt to fit `target_ratio` (default
   0.5) of the budget, so the result lands well under `max_tokens` (idempotent).
   This mirrors the hermes shape: `threshold_tokens ≈ context_window × percent`.

**Guidance:** for ReAct developers, prefer `SummaryContextEngine` with a **token
budget** (`max_tokens=`, e.g. ~50% of the model's context window); reserve
`WindowedContextEngine` (drop) for genuinely disposable spans. Message-count mode
(`max_messages=`) stays for offline/deterministic tests where token estimation
adds nothing.
