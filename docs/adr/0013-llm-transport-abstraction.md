## ADR-0013: Lightweight LLM transport abstraction

**Status**: Accepted (2026-06-23, v1.0)

**Reverses (in part)**: [CLAUDE.md §1](../../CLAUDE.md) prior non-goal
"不做 LLM provider 抽象". The non-goal is replaced with a *bounded* goal —
see "Decision" below.

**Borrowed from**: hermes-agent `agent/transports/{base,types}.py` per
[ADR-0007](0007-borrow-not-integrate-hermes.md). Reimplemented in this
repo; we do not import hermes.

**Companion**: [ADR-0001](0001-langgraph-as-orchestrator.md) ·
[ADR-0002](0002-reviewer-protocol.md)

## Context

For v0.1 — v0.5 the project flatly forbade an LLM provider abstraction.
Reasoning at the time: LangChain's `BaseChatModel` already abstracts the
provider, and adding a second wrapper would be wasted code.

By v1.0 the picture has changed:

1. **Cross-provider hacks already exist**, just scattered.
   `agent_room/llm/_retry.py` wraps `with_structured_output` in a
   parser-error retry (P3-A finding from real DeepSeek/Ark traffic).
   Reviewer / planner_gate / reviewer_two_call each independently call
   `bindings.resolve(role).with_structured_output(...)` then pass it
   through `retry_on_parser_error`. The pattern is duplicated three
   times and growing — every new structured-output call site has to
   remember the wrap.

2. **More differences are coming**. v1.0 production scope adds
   prompt-caching headers (Anthropic-style ephemeral cache control),
   reasoning_effort knobs (Claude / o-series), thought-signature replay
   (Gemini 3), and provider-specific message sanitisation. Doing each
   of these inline in the call site repeats the v0.x pain of `if
   "claude" in model: ...` blocks.

3. **The reviewer protocol is a contract**. ADR-0002 made
   `with_structured_output(ReviewerDecision)` the canonical reviewer
   interface. That is *already* a transport-layer concern — it shapes
   the wire request and parses the wire response. Pretending otherwise
   makes the seam invisible to readers.

4. **hermes-agent has paid the abstraction tax for us**. The 89-line
   `agent/transports/base.py` defines a four-method shape
   (`convert_messages → convert_tools → build_kwargs →
   normalize_response`) that has held up across Anthropic, Bedrock,
   Codex, Gemini and 16 OpenAI-compatible providers. We can borrow the
   *shape* without borrowing the *scope*: hermes ships ~3700 lines of
   transports; we need ~300.

The cost of a thin abstraction is now lower than the cost of
duplicating cross-provider knowledge across role nodes.

## Decision

### 1. Introduce `agent_room/llm/transport.py`

A `Transport` Protocol with three methods that cover every current
call site:

```python
class Transport(Protocol):
    async def invoke(
        self, role: Role, messages: Sequence[BaseMessage], *,
        tools: Sequence[BaseTool] | None = None,
    ) -> NormalizedResponse: ...

    async def astream(
        self, role: Role, messages: Sequence[BaseMessage], *,
        tools: Sequence[BaseTool] | None = None,
    ) -> AsyncIterator[StreamChunk]: ...

    async def structured(
        self, role: Role, schema: type[BaseModel],
        messages: Sequence[BaseMessage], *,
        attempts: int = 2,
    ) -> BaseModel: ...
```

`NormalizedResponse` and `StreamChunk` are frozen dataclasses with the
small shared field set every consumer reads (`content`, `tool_calls`,
`finish_reason`, `usage`); provider-specific extras live in an opaque
`provider_data: dict[str, Any] | None`. We borrow this exact pattern
from hermes' [`types.py`](../../../hermes-agent/agent/transports/types.py)
because we already know the alternatives — branching on `api_mode` in
business code, or polluting the shared type with provider fields —
don't hold up at scale.

### 2. Default implementation: `LangChainTransport`

Wraps the existing `RoleBindings` and delegates to `BaseChatModel`.
This is the sole implementation v1.0 ships; it preserves bit-for-bit
behaviour for v0.1 — v0.5 tests. `structured()` internally calls
`with_structured_output(...)` then forwards through
`retry_on_parser_error` — the three duplicated wraps in
`reviewer.py` / `planner_gate.py` / `reviewer_two_call.py` collapse to
one.

### 3. The seam, not the implementations

We do **not** ship per-provider transports (`AnthropicTransport`,
`OpenAITransport`, etc.) in v1.0. The Protocol is the contract; the
LangChain implementation covers all current providers. A second
implementation only lands if we hit something LangChain genuinely
can't express (e.g. Codex Responses API doesn't fit `BaseChatModel`'s
shape). The non-goal "不做 LLM provider 抽象" is replaced with the
positive constraint **"≤ 3 files, ≤ 300 lines, ≤ 2 implementations"**,
codified in CLAUDE.md §1.

### 4. Migration is mechanical

Each role node currently does:

```python
llm = bindings.resolve(role)
response = await llm.ainvoke(messages)
```

becomes:

```python
response = await transport.invoke(role, messages)
```

and the structured-output sites become `await transport.structured(role,
ReviewerDecision, messages)`. `RoleBindings` is unchanged; only its
*direct callers* shift.

`AgentRoomService.__init__` accepts an optional `transport` keyword
(defaults to `LangChainTransport(bindings)` if omitted) so test fixtures
can inject a stub transport without building a graph.

### 5. CLAUDE.md update

§1 non-goals lose "不做 LLM provider 抽象"; §2 禁止 line replaces "直接
用 OpenAI/Anthropic SDK 绕过 LangChain" with "不经过 transport 直接调
LangChain 的 `BaseChatModel`"; §4.5 routes business code through
`transport.invoke(role, ...)` and points here.

## Rejected alternatives

- **Keep the no-abstraction rule**. The duplication of
  `retry_on_parser_error` wraps and the predicted prompt-cache /
  reasoning-effort growth make this strictly worse than a thin seam.
- **Adopt hermes-agent's full transport stack verbatim**. ~3700 lines
  for capabilities we don't need (Codex app-server session, Bedrock,
  thought-signature replay) — violates "用最少的代码". The 89-line
  *base* and the 174-line *types* are the only useful borrows; the
  concrete transports stay LangChain-shaped for v1.0.
- **Per-call decorator pattern (no Protocol)**. Already tried — it's
  what `retry_on_parser_error` is, and the duplication problem this
  ADR exists to solve was *caused* by that pattern.

## Out of scope (deferred)

- **Provider-native transports** (Anthropic / OpenAI direct SDK). Wait
  until LangChain demonstrably blocks something we need.
- **Token-level prompt cache control**. v1.x — once we have
  Prometheus metrics it'll be obvious where the wins are.
- **Streaming `tool_call` deltas** in `StreamChunk`. v1.0 emits text
  deltas only; tool deltas come when a real consumer asks for them.
- **Provider catalog as YAML plugins** (hermes
  `plugins/model-providers/*/plugin.yaml`). Out of scope: our model
  selection is one env var per role plus a default; YAML catalog is a
  hermes-scale need, not ours.

## Consequences

- **One place to add reviewer-protocol or cache control**.
  `Transport.structured` / `Transport.invoke` is now the unique
  insertion point; future changes don't ripple through six role files.
- **Test surface stays equivalent**. `LangChainTransport` is bit-equal
  to v0.5 behaviour because it is literally the same calls, plus the
  `retry_on_parser_error` wrap that was already universal.
- **Independence story unchanged**. No hermes import, no new top-level
  dependency. Adds two files under `agent_room/llm/` (modules already
  under the independence-driver scan).
- **CLAUDE.md is honest**. The constitution previously claimed
  "we don't abstract providers"; in practice we already had three
  scattered cross-provider concerns. The rule is now scoped, not
  absolute, and the scope is enforced by an explicit ceiling.
