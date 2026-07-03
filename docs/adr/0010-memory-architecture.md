# ADR-0010: Memory architecture — two layers, one tool, frozen snapshot

**Status**: Accepted (2026-06-14, v0.5)

**Companion**: [ADR-0007 — borrow-not-integrate hermes-agent](0007-borrow-not-integrate-hermes.md)

## Context

agent-room finishes a task, the process exits, and the next run remembers
nothing from before. LangGraph's SQLite checkpointer recovers an in-flight
task, but does not carry user preferences, project facts, or "did we
already discuss X" across distinct task invocations.

`PLAN.md §5` originally sketched a single `MemoryProvider` ABC with `prefetch`
+ `remember`. Distilling hermes-agent's working implementation revealed
three load-bearing details that the sketch glossed over: **two layers**
(curated facts vs transcript log) **one tool** (write-only LLM API), and
**frozen snapshot** (mid-session writes do not mutate the prompt the LLM
sees).

## Decision

### Two memory layers

1. **Curated** — `MEMORY.md` (general facts, 2200 char cap) + `USER.md`
   (user preferences / identity, 1375 char cap), both newline-`§`-newline
   delimited. Written by the LLM via the `memory` tool. Read once at
   session start into the system prompt.

2. **Transcript** — every assistant / user / tool message appended to a
   SQLite log with FTS5 full-text index. Read via `prefetch(query)` per
   turn; matches injected into system prompt under `<memory-context>`
   fence.

The two layers solve different problems. Curated is **assertion** — the
LLM's deliberate decision to remember a fact. Transcript is **search** —
"did I see X before?" Bundling them under one Protocol means the
framework cares about one object, but the LLM-facing tool surface only
touches the curated layer (see below).

### One LLM-facing tool

The `memory` tool is a single `BaseTool` with two axes:

```python
memory(action: "add" | "replace" | "remove",
       target: "memory" | "user",
       content: str,
       old_substring: str | None = None)
```

`recall` is **not** exposed. Reasoning:

- LLMs over-call recall tools on every turn, burning tokens and adding
  latency. Auto-prefetch on every LLM call (with the user query as the
  natural search key) gets the same result with zero LLM-side decision.
- A 4-action surface (`add` / `replace` / `remove` / `recall`) is harder
  for smaller models than a 3-action one. Ablating `recall` empirically
  improves write quality.

This mirrors hermes-agent's `memory` tool exactly so prompt-engineering
investment transfers.

### Frozen snapshot

`MemoryProvider.system_prompt_block()` reads disk **once** at
`initialize()` and returns the same string for the entire session. Mid-
session writes via `add` / `replace` / `remove` update the disk file (so
peer processes / next session see them) but the live prompt is bit-for-
bit stable.

Why this matters: Anthropic / DeepSeek / OpenRouter all charge dramatically
less for cached prefix tokens. A mutating system prompt invalidates the
cache on every turn. Empirically: a 2000-token system prompt re-cached
every turn over a 30-turn ReAct session burns ~60K cached-token bills;
frozen-snapshot pattern collapses that to ~2K.

The cost is one session of staleness — a fact written in turn 3 is
visible to the LLM only in the next session. The transcript layer
(`prefetch`) covers in-session recall, so this isn't a real loss.

### Tool-side rejection over LLM-side validation

Curated writes go through a threat-pattern scan (prompt-injection,
exfiltration verbs, invisible Unicode). Rejection is **returned to the
LLM as a tool-result string**, not raised as an exception. The LLM sees
"rejected: content rejected by threat scan: pattern '...' matched" and
can retry with safer phrasing.

This keeps the system-prompt input space safe (no injection makes it to
disk) without making the LLM responsible for self-policing — historically
a fragile path.

## Consequences

**Positive**

- Cross-session recall works without any external dependency (SQLite is
  in stdlib, FTS5 is a default extension on Python 3.13's bundled SQLite).
- Tool surface is small enough to fit in a single line of the developer
  system prompt.
- Default `NoOpMemoryProvider` keeps v0.1-v0.4 behavior bit-for-bit
  unchanged — opt-in only.
- File-based curated layer is human-readable and human-editable (e.g.,
  user can hand-edit `MEMORY.md` between sessions to seed facts).

**Negative**

- Frozen-snapshot means the writing LLM doesn't see its own write
  reflected in the prompt — feels slightly weird in dev, but the tool's
  `ok: added entry to memory` confirmation message bridges that gap.
- Threat-pattern list is conservative; legitimate content can occasionally
  be rejected (e.g., a fact that mentions `curl https://...` for some
  legitimate reason). Users can extend the list via subclassing or wait
  for v0.6 allow-list refinements.
- FTS5 default tokenizer is poor on CJK — we fall back to LIKE substring
  matching for pure-CJK queries. Proper tokenizer (jieba / lindera) is
  v0.6+.

**Defer to v0.6+**

- TTL / age-based forgetting (`AGENT_ROOM_MEMORY_TTL_DAYS`).
- Cross-node memory writes — v0.5 only the developer node has the tool;
  reviewer / planner write paths come later.
- GUI for editing MEMORY.md / USER.md (Vue UI, v1.0).
- Disk-backed `SpillStore` integration with the transcript log (v0.6 task
  alongside §3.4 spill retrieval).

## Alternatives rejected

**Single-layer transcript only** — works for "did we discuss X?" but
fails the "remember user prefers Python 3.11+" use case unless the LLM
discovers and re-asserts the preference every session, which it
inconsistently does.

**Single-layer curated only** — works for preferences but loses the
ability to search prior detailed conversations. The transcript log costs
~50 LOC of SQLite to add; not worth giving up.

**Vector embeddings** — would require an embedding model dependency (the
whole reason ADR-0007 has us avoiding hermes-agent's `sentence-
transformers` chain). FTS5 + LIKE is good enough for v0.5; revisit if
recall quality measurably suffers.

**No frozen snapshot** — see "why this matters" above. Prefix-cache
invalidation cost is real and load-bearing.
