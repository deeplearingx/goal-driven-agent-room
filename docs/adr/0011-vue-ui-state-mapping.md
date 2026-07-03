## ADR-0011: Vue UI ↔ server state mapping & sessions thin shell

**Status**: Accepted (2026-06-22, v1.0 §6.1)

**Companion**: [v1.0-vue-ui-reconciliation audit](../v1.0-vue-ui-reconciliation.md)
· [ADR-0002 — reviewer protocol](0002-reviewer-protocol.md)
· [ADR-0007 — borrow-not-integrate hermes](0007-borrow-not-integrate-hermes.md)

## Context

The PLAN §6.1 Vue UI reuse pulls the existing
[hermes-web-ui agent-room client](../../../hermes-web-ui/packages/client/src/api/hermes/agent-room.ts)
onto agent-room-py's HTTP surface. The 2026-06-14 reconciliation audit
recorded four large gaps:

| dim | server (this repo) | client (hermes-web-ui) |
|---|---|---|
| resource root | `task` | `session ⊃ tasks ⊃ runs ⊃ roleRuns` |
| status states | 4 | 12 |
| realtime | SSE (`/tasks/stream`) | 3 s polling |
| role binding | dataclass + env (per-process) | per-session DB rows + Profile |

The user's D1-D4 calls (2026-06-22) commit to **Option C "harder cut"**:
the server stays task-rooted with the smallest possible session shim,
and the frontend drops the views that don't fit.

This ADR pins three contracts so future work cannot drift:

1. what `sessions` means on this server,
2. how the frontend's 12 statuses are derived from the server's 4,
3. why `runs` / `roleRuns` / `role_bindings` tables stay out of v1.0.

## Decision

### 1. `sessions` is a tag, not a lifecycle owner

```sql
CREATE TABLE IF NOT EXISTS agent_room_sessions (
  id          TEXT PRIMARY KEY,           -- uuid4 hex
  name        TEXT NOT NULL,
  created_at  REAL NOT NULL                -- unix seconds
);
CREATE TABLE IF NOT EXISTS agent_room_session_tasks (
  session_id  TEXT NOT NULL,
  task_id     TEXT NOT NULL,
  attached_at REAL NOT NULL,
  PRIMARY KEY (session_id, task_id)
);
CREATE INDEX IF NOT EXISTS idx_agent_room_session_tasks_task
  ON agent_room_session_tasks(task_id);
```

That is the entire schema. The session has **no** `status`,
`autoDeliveryEnabled`, `metrics`, `config`, `role_bindings`, `runs`,
or `messages` of its own. Every dynamic property on
`AgentRoomSession` in the audit doc is dropped on the server side; the
frontend either derives it from the linked tasks or stops showing it.

The thin shell rules:

- **Truth lives in LangGraph checkpointer.** Adding a session row
  never persists task state — the existing `service.run` /
  `service.snapshot` / `service.resume` paths stay sole owner.
- **Sessions DB is a separate connection** sharing the same
  `db_path`. Tables are namespaced `agent_room_sessions*` and never
  collide with checkpointer tables (`checkpoints / writes / blobs`)
  or memory tables (`agent_room_memory_messages*`).
- **Session deletion does not delete tasks.** A `DELETE /sessions/{id}`
  removes the rows in `agent_room_sessions` + `agent_room_session_tasks`;
  the underlying checkpointer state is untouched. Reusing a `task_id`
  still works.

### 2. Status mapping (12 → 4 + Event[])

The frontend keeps its 12-state UI vocabulary (it is the human-facing
labelling) but **derives** every label from server-side primitives.
The server only ever emits four `TaskResult.status` values. The
frontend maps as follows:

| frontend status | server primitive | derivation |
|---|---|---|
| `created` | `status = running`, `events = []` | brand-new task, no node has run yet |
| `planned` | `running`, last event `node_end:planner` | planner finished, developer not yet started |
| `assigned` | `running`, last event `node_start:developer` | developer node entered |
| `in_progress` | `running`, last event `node_start:developer` or `token` from developer | developer streaming |
| `submitted_for_review` | `running`, last event `node_end:developer` | developer done, reviewer not yet started |
| `review_passed` | `running`, last event `node_end:reviewer`, `result.review.decision == "approved"` | reviewer approved, delivery pending |
| `review_rejected` | `running`, last event `node_end:reviewer`, `result.review.decision == "revision_required"` | reviewer rejected, dev rerun pending |
| `revision_required` | identical to `review_rejected`, may be the terminal label if `result.rounds == max_revisions` and `status = failed` | hold for one developer round; if `failed` the badge stays on `revision_required` |
| `delivering` | `running`, last event `node_start:delivery` | delivery node entered |
| `completed` | `status = completed` | terminal |
| `failed` | `status = failed` | terminal |
| `need_user_decision` | `status = awaiting_user`, `result.review.decision == "need_user_decision"` | resume via `POST /tasks/{id}/resume` |

Properties:

1. **No new server state.** All twelve labels collapse into the four
   primitives plus the existing event log + `ReviewerDecision`.
   `agent_room/schemas.py::TaskResult.status` does not gain values.
2. **The map is one-way.** The frontend is the only consumer. The
   server does not echo back the 12-name vocabulary anywhere — that
   would invite drift.
3. **Implementation is `if/else` on the frontend.** No state-machine
   library, no `taskStatusForUI(server)` adapter on the server.

The mapping table above is the single source of truth; both the
backend route docs and the frontend `taskStatusFromServer()`
helper reference it.

### 3. SSE is the only realtime channel

The frontend's polling implementation is replaced with an
`EventSource('/tasks/stream')` composable in v1.0. Reasons (recap from
chat decision 2026-06-22):

- The server already exposes a tested SSE feed (`agent_room/server/api.py`,
  `agent_room/events.py`), used by the CLI smoke harness end-to-end.
- The polling fallback existed in hermes-web-ui only because that codebase
  had no SSE; it is not a quality choice.
- Maintaining two data channels doubles the bug surface around state
  reconciliation (stale snapshot vs latest event).

`GET /tasks/{task_id}` remains for snapshot rehydration when the user
opens a previously-running task without an event stream attached.

### 4. Role bindings stay process-scoped (no `role_bindings` table)

`RoleBindings` ([agent_room/config.py](../../agent_room/config.py)) is
the v1.0 source of truth: a process-level dataclass populated from
environment variables (`AGENT_ROOM_{ROLE}_MODEL` etc.). The frontend
gets a **read-only** view of the current mapping by extending
`/healthz`:

```json
GET /healthz
→ {
    "status": "ok",
    "role_bindings": {
        "planner": "DeepSeek-V4-Pro",
        "developer": "DeepSeek-V4-Pro",
        "reviewer": "DeepSeek-V4-Pro",
        "delivery": "DeepSeek-V4-Pro"
    }
}
```

The frontend `AgentRoomRoleBindingModal.vue` is **not** migrated. A
static read-only panel reads from `/healthz`. Editing role→model
bindings in the UI is deferred to v1.1, where a multi-tenant /
multi-profile design (analogous to hermes-web-ui's `Profile` table)
gets a separate ADR.

### 5. Frontend views NOT migrated

The following hermes-web-ui surfaces depend on entities that v1.0
deliberately does not produce; they are deleted from the migrated
package, not stubbed:

- `AgentRoomRunsView.vue` and any role-run cards (no `runs` / `roleRuns`).
- `AgentRoomRoleBindingModal.vue` (D4 deferral).
- `AgentRoomSessionMetrics*.vue` (no per-session metrics; Prometheus
  takes this role at the server level — see PLAN.md §6.1 production
  bullet).
- `getSessionMetrics` / `getSessionConfig` / `updateSessionConfig`
  client functions and any view that consumes them.

### 6. Frontend views migrated

Kept and rewired against the new shape:

- `AgentRoomChatView.vue` / `AgentRoomMessageList.vue`
- `AgentRoomTaskPanel.vue` (status switch → if/else against this ADR's table)
- `AgentRoomArtifactsView.vue` (reads `TaskResult.artifacts`)
- `AgentRoomTimelineView.vue` (reads `Event[]` instead of
  `workflowEvents`)
- `office scene` (Phaser, presentation-only, zero dependency on shape)
- `CreateTaskModal.vue` / `ReviewDecisionModal.vue`

## Consequences

### Positive

- Server stays minimal: one ~1KB DAO module + one ~2KB router file +
  the `/healthz` extension. No state machine grows. SemVer v1.0
  surface remains the existing `/tasks*` routes; `/api/agent-room/*`
  is documented as a v1.0-only compatibility surface that can be
  retired in v1.x without breaking task-rooted clients.
- Frontend deletes ~40% of the agent-room client (Runs/RoleBinding/
  Metrics + polling glue). Bug surface shrinks; SSE wiring lands once.
- Status-mapping table is the single artifact reviewers check when
  the frontend "shows the wrong badge" — debugging UI desync is
  one Read of this ADR.

### Negative

- The frontend cannot show "all sessions across users / projects" —
  by design, since v1.0 has no users / projects concept. v1.1
  multi-tenant ADR will revisit.
- `revision_required` and `review_rejected` collapse to the same
  derivation; the label is contextual ("which one is this badge?")
  on the frontend, gated on whether the developer node has been
  re-entered. Frontend tests pin both directions.
- Migrating someone from hermes-web-ui's session metrics to
  Prometheus is a step change, not gradual — there is no per-session
  metrics endpoint on the server.

### Rejected alternatives

- **Option A (full backend shim)** — adding `runs` / `roleRuns` /
  `metrics` tables to mirror hermes-web-ui's surface. Rejected: the
  server's LangGraph events already encode those execution-level
  details; an extra projection layer would double-write the same
  history into a less expressive shape.
- **Option B (rewrite Vue against task-rooted from scratch)** —
  rejected on cost. The audit estimated 10-14 days for B versus 6
  days for the harder-cut Option C; the visual assets (Phaser scene,
  message list, artifact viewer) are reusable as-is.
- **Mid-version: add a `tasks.ui_status: TaskStatus` column** —
  rejected because (a) it duplicates derivable information, (b) it
  forces the server to bless the frontend's vocabulary, and (c) it
  makes the SemVer surface harder to evolve once the frontend
  re-shapes labels.

## References

- [docs/v1.0-vue-ui-reconciliation.md](../v1.0-vue-ui-reconciliation.md) — audit + path comparison
- [agent_room/server/api.py](../../agent_room/server/api.py) — current task-rooted routes
- [agent_room/schemas.py](../../agent_room/schemas.py) — `TaskResult`, `ReviewerDecision`, `Event`
- [agent_room/events.py](../../agent_room/events.py) — SSE event names
- PLAN.md §6.1 — D1-D4 chat decisions of 2026-06-22
- PLAN.md §8 — §6.1.1-§6.1.7 subtask cards
