# Security & privacy

Companion to [CLAUDE.md](CLAUDE.md) §2 (which keeps the four-line summary).
This file holds the details and the reporting flow.

## Secrets

- API keys live in `.env` or environment variables only. **Never** commit a
  populated `.env`. The committed [`.env.example`](.env.example) lists the
  expected variables.
- The default SQLite checkpoint database (`./agent_room.db`) is in
  [`.gitignore`](.gitignore) and stores user-provided task descriptions
  verbatim. **Don't share a single DB across tenants.**
- Do not echo secret values back in logs / SSE / responses. Reference them
  by key name (e.g. `ANTHROPIC_AUTH_TOKEN`) rather than value.

## Tool-using developer (ReAct sandbox)

The default server graph (`full_react`) grants the developer node the built-in
tools so it can read/write files and run a shell command. Tools are a foothold
onto the host, so the envelope is defined in one place
([`agent_room/server/react_runtime.py`](agent_room/server/react_runtime.py)) and
surfaced at `GET /healthz` under `tool_envelope` so operators can verify it:

- **Filesystem confinement** — `read_text` / `write_text` / `glob` are rooted at
  `AGENT_ROOM_WORKSPACE` (default `./workspace`). `resolve_within_root` rejects
  `..` traversal, absolute escapes, and symlink escapes.
- **Shell allowlist (deny-by-default)** — `shell` only runs commands whose head
  token is in `AGENT_ROOM_SHELL_ALLOWLIST` (default `pytest,python,python3,ruff`).
  Setting it empty (`AGENT_ROOM_SHELL_ALLOWLIST=""`) drops the shell tool, and the
  server drops `shell` from the graph spec too so the build can't reference it.
- **Shell execution confinement** — the shell's `cwd` is pinned to the workspace,
  so commands never run against the repo or home directory.
- **Timeout** — every shell call is bounded (30s).
- **Bounded loop** — `max_dev_rounds` caps the ReAct tool-calling loop.

`AGENT_ROOM_TOOL_MODE` controls how write/shell pass the permission filter:
`unrestricted` (default; autonomous within the sandbox), `approval` (per-call
human gate, resume via `POST /tasks/{id}/resume`), or `read_only` (only
`read_text`/`glob`). Point `AGENT_ROOM_WORKSPACE` at a throwaway directory —
the developer may overwrite files inside it. Set `AGENT_ROOM_GRAPH=full` to fall
back to the tool-less developer entirely.

## Goal mode: verify_command + verify_files (PLAN.md goal-mode section)

`AGENT_ROOM_GRAPH=goal` runs a develop→verify loop that exits when the
task's `verify_command` exits 0. Two trust-boundary points:

- **verify_command is caller-supplied code execution — bounded by the same
  envelope as the developer's shell tool.** It passes the identical
  `AGENT_ROOM_SHELL_ALLOWLIST` head-token check + shell-metacharacter
  rejection, runs with cwd pinned to the task workspace, and is killed after
  60s. Anyone who can `POST /tasks` could already run allowlisted commands
  indirectly through the developer's shell tool, so verify_command adds no
  new execution surface — but operators who tighten the allowlist tighten
  both at once, deliberately.
- **verify_files is the anti-tamper mechanism, and its absence is a real
  risk.** Files listed there are re-written into the workspace before EVERY
  verification run, so a developer that edits the tests to make them pass
  (a real, repeatedly-observed coding-agent behavior) has its edits
  overwritten before the oracle executes. A goal task whose oracle depends
  on files NOT protected this way accepts the tamper risk itself — put the
  check logic in `verify_files`, not in files the developer is expected to
  edit.

The stuck-supervisor's `ask_user` action writes a `need_user_decision`
review — deliberate reuse of the reviewer contract so the existing
pause/resume machinery (and its security properties) applies unchanged.

## MCP (Model Context Protocol) tools

Off by default. Setting `AGENT_ROOM_MCP_SERVERS` (a JSON map of server name →
[langchain-mcp-adapters](https://github.com/langchain-ai/langchain-mcp-adapters)
connection config) loads that server's tools into the developer's registry
alongside the built-ins — the operator opts in to whichever server(s) they
configure, not the LLM.

- **Same permission filter, no bypass** — MCP tools have no `read_only`
  metadata, so `tools/policy.py::is_read_only` conservatively treats them like
  `write_text`/`shell`: **dropped under `AGENT_ROOM_TOOL_MODE=read_only`**,
  gated per-call under `approval`, passed through under `unrestricted`.
- **Trust boundary is the operator's, not the LLM's** — the *set* of available
  MCP tools is fixed by the server list you configure; the LLM only picks
  *which* configured tool to call and with what arguments, same as any other
  tool. Treat each configured MCP server as trusted code: a malicious or buggy
  server can do whatever its own process/network access allows, independent of
  agent-room's sandbox (the filesystem/shell confinement above only applies to
  the *built-in* tools, not to what an MCP server does on its own side).
- **Fail loud on misconfiguration** — malformed `AGENT_ROOM_MCP_SERVERS` JSON,
  or an MCP server that's unreachable at startup, raises rather than silently
  starting with a missing tool ecosystem.
- **Discoverable** — `GET /healthz.tool_envelope` reports the configured
  `mcp_servers` and the `mcp_tools` names actually loaded, so an operator can
  verify what's live without reading logs.

## Per-task budget ceiling (v1.x §6.9-2)

Off by default (`AGENT_ROOM_MAX_TOKENS` / `AGENT_ROOM_MAX_TOOL_CALLS` /
`AGENT_ROOM_MAX_COST_USD` all unset = unlimited). When set, one shared
[`BudgetTracker`](agent_room/budget.py) accumulates usage across every role in
a task run; exceeding a ceiling raises inside the LLM transport, halting the
graph and surfacing a `task_error(budget_exceeded=true)` SSE frame instead of
letting a runaway task (most realistically the developer's ReAct tool loop)
keep spending.

- **Server-operator-level only** — one ceiling applies uniformly to every
  task; there is no per-request override. `GET /healthz.budget` reports the
  active ceiling.
- **Known scope limit, documented not hidden** — only `Transport.invoke()`
  calls (planner / developer / delivery) contribute token usage.
  `Transport.structured()` calls (reviewer / planner_gate / reviewer_two_call)
  don't, because `with_structured_output` doesn't surface `usage_metadata` on
  the path this transport uses. `max_tool_calls` still fully covers the
  developer's tool loop — the highest-risk runaway-cost path — regardless.
- **Not resume-safe** — the tracker lives in-process per compiled graph, not
  in `TaskState`/the checkpointer. A task that pauses (`need_user_decision` or
  tool-call `approval`) and is later resumed gets a **fresh** tracker (usage
  from before the pause isn't carried forward). A task that runs straight
  through without pausing — the default `unrestricted` + `full_react` path —
  has fully cumulative, correct enforcement.

## Content guardrail (v1.x §6.9-3)

Off by default (`AGENT_ROOM_GUARDRAIL=off`). [`agent_room/guardrail.py`](agent_room/guardrail.py)
does pattern-based scanning (prompt-injection / role-hijack / exfil-command
shapes, plus a small email/API-key/AWS-key set — lightweight matching, not
full PII/NER) at 4 checkpoints, each picked because it's a real, reachable
place content crosses a trust boundary:

- **input** — `POST /tasks` / `POST /tasks/stream`, before the graph starts.
  Cheapest rejection: `mode="block"` returns HTTP 400, the graph never runs.
- **tool_call** — `write_text`'s `content` / `shell`'s `command`, scanned
  before content leaves the sandbox (write to disk / exec). Deliberately
  scoped to these two built-ins, not a universal per-tool wrapper — the
  generic hook (`ToolNode.awrap_tool_call`) is already claimed by
  `AGENT_ROOM_TOOL_MODE=approval`'s human-in-the-loop gate, and composing the
  two wrappers is out of scope for this pass.
- **tool_response** — the developer ReAct loop, scanned before a tool's
  result re-enters the LLM's context. This is the highest-value checkpoint:
  it catches a malicious/compromised **MCP server** trying to prompt-inject
  via its tool's return value — and unlike `tool_call`, it covers every tool
  uniformly (built-in or MCP), not just the two write-capable built-ins.
- **output** — the delivery role's final handoff text, the last point before
  content leaves the system.

`mode="warn"` scans and records an `Event(type="guardrail_triggered", ...)`
into `TaskState.events` without blocking. `mode="block"` raises
`GuardrailTripwire` from inside the checkpoint — for the 3 in-graph
checkpoints (tool_call / tool_response / output) this propagates exactly like
`BudgetExceededError` (the node doesn't catch it) and surfaces as
`task_error(guardrail_blocked=true)`; a block hit inside the graph does **not**
get a `TaskState.events` entry (the node never returns) — same asymmetry as
budget, not a new gap. `GET /healthz.guardrail` reports the active mode.

The pattern list is shared with (not duplicated from) [curated memory's write
scan](agent_room/memory/curated.py) — one canonical list, imported back.
[`memory/scrubber.py`](agent_room/memory/scrubber.py) is a different concern
(streaming `<memory-context>` fence suppression) and isn't part of this layer.

## Hybrid semantic/vector memory recall (v1.x §6.9-4)

Off by default (`AGENT_ROOM_MEMORY_VECTOR=0`); only takes effect when
cross-session memory itself is on. When on, [`TranscriptStore`](agent_room/memory/fts.py)
also indexes every message in a sqlite-vec KNN table
([`VectorStore`](agent_room/memory/vector.py)) and fuses that with the
existing FTS5/LIKE lexical search via reciprocal rank fusion — no new
network calls, no new secrets, same disk/SQLite trust boundary as the rest
of cross-session memory (below).

- **Not a deep/transformer embedding, and that's documented, not hidden** —
  the bundled [`HashingEmbeddingBackend`](agent_room/memory/embedding.py) is
  deterministic character n-gram feature hashing (stdlib `hashlib` + `math`
  only, no ML dependency, no API key). It buys character-level similarity
  over FTS5's word-token matching — most valuable for CJK, where FTS5's
  tokenizer splits per-character and loses phrase structure — but it cannot
  capture true synonym/semantic paraphrase with zero character overlap.
  `EmbeddingBackend` is a one-method Protocol; swapping in a real
  transformer/API embedding is a drop-in implementation — see the Qdrant
  backend below (v1.x §6.15), which is exactly that drop-in.
- **NoOp-default, same shape as every other toggle** — `Settings.embedding_backend()`
  returns `None` when `AGENT_ROOM_MEMORY_VECTOR` is unset, and `TranscriptStore`
  only attaches the sqlite-vec extension / creates the KNN table when its
  `embedding` constructor argument is not `None`. Off means the vector layer
  is never touched, not merely idle.
- **Fail loud on unsupported SQLite builds** — loading the `sqlite-vec`
  extension needs a Python `sqlite3` build with loadable-extension support.
  If that's unavailable, `VectorStore.attach()` raises at startup rather than
  silently degrading recall quality.
- **Discoverable** — `GET /healthz.tool_envelope` reports `memory_vector`
  (bool) and `memory_vector_backend` (`"noop"` or `"hashing"`).

## Pluggable vector backend (Qdrant) + physical per-tenant isolation (v1.x §6.15)

Two additions on top of §6.9-4, both off by default:

**Qdrant vector backend** (`AGENT_ROOM_VECTOR_BACKEND=qdrant`, requires
`pip install 'agent-room[vector]'`): swaps `HashingEmbeddingBackend` for
[`FastEmbedBackend`](agent_room/memory/fastembed_backend.py) (a real
transformer embedding, default `jinaai/jina-embeddings-v2-base-zh` — bilingual
Chinese-English, matching this project's actual mixed-script content) and
sqlite-vec for [`QdrantVectorIndex`](agent_room/memory/qdrant_index.py).

- **Embedded by default, no server to run** — `AGENT_ROOM_QDRANT_PATH` is a
  local file-based Qdrant index (`QdrantClient(path=...)`), matching the
  project's zero-external-service posture. `AGENT_ROOM_QDRANT_URL` opts into
  a real server/cloud instance — once set, embedding vectors (and, over the
  wire, the underlying text content) leave this process for that address.
  Treat a configured Qdrant server the same as a configured MCP server: trust
  its own process/network access, independent of this sandbox.
- **Model choice is a real quality/latency tradeoff, not a free upgrade** —
  the default model costs tens of milliseconds of CPU inference per call.
  `prefetch()` is awaited synchronously before every developer LLM turn, so
  this is real per-turn latency; `asyncio.to_thread` (in `TranscriptStore`)
  keeps it from blocking *other* concurrent tasks' event-loop work, but
  doesn't make the calling task's own turn faster.
- **Fail loud on model/collection dimension mismatch** — `QdrantVectorIndex.initialize()`
  compares an existing collection's configured vector size against the
  active embedding model's output dimension and raises rather than silently
  serving corrupted-looking recall if an operator changes
  `AGENT_ROOM_EMBEDDING_MODEL` after the collection was already populated.

**Physical per-tenant persistence isolation** (`TaskRequest.tenant_id`,
default `"default"`): each tenant gets its own `agent_room.db` (checkpoints +
curated MEMORY.md/USER.md + transcript FTS5/vector — all colocated the same
way the single-tenant path colocates them) under
`<db_path's dir>/tenants/<tenant_id>/`, opened lazily on first use by
[`TenantCheckpointerPool`](agent_room/server/tenancy.py). `tenant_id="default"`
keeps the original `db_path` untouched — zero behavior change for existing
single-tenant deployments.

- **Physical, not row-filtered, isolation** — there is no shared table with a
  `tenant_id` WHERE clause to get wrong; each tenant's checkpoints/memory live
  in a completely separate SQLite file (and, under the Qdrant backend, a
  separate embedded path or collection). This was a deliberate simplification
  over an earlier "shared table + filter column" design once it became clear
  giving each tenant its own `db_path` isolates every layer for free (see
  PLAN.md §6.15's design notes).
- **`agent_room` does not authenticate callers — this is the trust boundary
  that must not be assumed away.** `tenant_id` is whatever the request body
  says it is. This mechanism guarantees that *once told* which tenant a
  request belongs to, that tenant's data is physically separate from every
  other tenant's — it does **not** verify *who* is making the request. A
  real multi-tenant deployment needs an auth gateway in front that derives
  `tenant_id` from a validated credential and forces it into the request,
  never trusting a client-supplied value directly. The `GET/POST /tasks/{id}*`
  ownership check (comparing a known task's recorded `tenant_id` against the
  request's) catches *accidental* cross-tenant access from an application bug
  or stale UI state — it is defense-in-depth, not authentication, and does
  not stop a caller that deliberately lies about its own tenant_id.
- **No idle-tenant eviction** — every tenant's checkpointer connection stays
  open for the server's lifetime once first used. No measured need for an
  LRU/close policy at this project's scale; a high-tenant-churn production
  deployment would want one.

## Cross-session memory (v0.5)

When `AGENT_ROOM_MEMORY` is on (default), the server persists two layers next to
`AGENT_ROOM_DB`: curated `MEMORY.md` / `USER.md` facts and a SQLite+FTS5
transcript of messages. This means **task content survives across runs** — treat
the DB and the `memory/` directory as private, single-tenant data (CLAUDE.md §2:
"SQLite DB 不跨租户"). Set `AGENT_ROOM_MEMORY=0` to disable persistence entirely.

The developer's `memory` tool can write facts autonomously. Writes pass a
threat-pattern scrubber + char cap before landing on disk
([`agent_room/memory/scrubber.py`](agent_room/memory/scrubber.py),
[`curated.py`](agent_room/memory/curated.py)), and curated facts use a frozen
snapshot — a write is only reflected in the system prompt on the next session,
not retroactively. The `<memory-context>` prefetch fence is trusted-to-the-LLM
and must not leak verbatim through streaming (see below).

## SSE streaming

The SSE layer does **not** filter sensitive content out of model output.
Any redaction / DLP / PII scrubbing is the **caller's** responsibility.
The in-repo UI (`/ui`) is the canonical example of a UI-side consumer that
must not expose raw events to untrusted viewers.

The `<memory-context>` fence used in cross-session memory prefetch (v0.5)
is similarly trusted-to-the-LLM but **must not** leak verbatim through
streaming. See [`agent_room/memory/scrubber.py`](agent_room/memory/scrubber.py)
and PLAN.md §5.7 for the streaming-context scrubber.

## Reporting

Security issues: open a private GitHub Security Advisory rather than a
public issue. Include reproduction steps and affected version(s).
