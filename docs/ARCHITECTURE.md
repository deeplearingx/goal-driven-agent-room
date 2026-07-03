# Architecture — agent-room

Companion to [CLAUDE.md](../CLAUDE.md) (constitution) and
[PLAN.md](../PLAN.md) (roadmap). This document describes the package
layout. Architectural *invariants* live in CLAUDE.md §4 — those are the
rules; this is the map.

## Package layout

```
agent-room-py/
├── agent_room/                 # main package
│   ├── __init__.py             # public API surface (kept thin)
│   ├── schemas.py              # Pydantic models (cross-boundary data)
│   ├── state.py                # TaskState TypedDict + reducers
│   ├── config.py               # Settings + RoleBindings (env fallback chain)
│   ├── graph.py                # StateGraph wiring + checkpointer ctx mgr
│   ├── service.py              # AgentRoomService façade (run/stream/resume/snapshot)
│   ├── events.py               # astream_events → UI-friendly dicts
│   ├── cli.py                  # Typer entrypoint
│   ├── routers.py              # graph routing helpers
│   ├── spec.py                 # GraphSpec + presets (ADR-0008)
│   ├── presets/                # YAML preset DAGs (full / dev_review / solo / …)
│   ├── roles/                  # one role per file
│   │   ├── planner.py
│   │   ├── planner_gate.py
│   │   ├── developer.py
│   │   ├── developer_react.py
│   │   ├── reviewer.py
│   │   ├── reviewer_two_call.py
│   │   └── delivery.py
│   ├── prompt/                 # layered prompt builder (v0.4 §4.4)
│   ├── context/                # ContextEngine ABC + Windowed/Summary engines
│   ├── memory/                 # MemoryProvider (curated MD + FTS5 transcript)
│   ├── tools/                  # built-in tools + registry + policy + spill
│   ├── llm/                    # transport seam + parser-error retry (ADR-0013)
│   └── server/
│       ├── api.py              # FastAPI app + SSE
│       ├── sessions.py         # sessions thin shell DAO (ADR-0011)
│       ├── compat_v1.py        # /api/agent-room/* compat surface
│       └── ui/                 # in-repo thin UI (ADR-0012)
├── tests/                      # pytest, offline (FakeListChatModel)
├── examples/                   # runnable PoCs, each < 80 lines
├── docs/
│   ├── ARCHITECTURE.md         # this file
│   ├── adr/                    # decision records, NNNN-title.md
│   ├── tools.md                # tool development guide
│   ├── context.md              # context-engine design notes
│   └── memory.md               # memory architecture user view
├── CLAUDE.md                   # constitution
├── PLAN.md                     # detailed roadmap
├── CONTRIBUTING.md             # git / commit / PR conventions
├── SECURITY.md                 # security & privacy notes
└── pyproject.toml
```

## Adding a new module — three-question gate

Before creating a new module under `agent_room/`, answer:

1. Could this fit inside `agent_room/roles/`?
2. Could it fit on `service.py` as a method?
3. Is a new module genuinely needed (not "for future flexibility")?

A new module is justified when **at least one** answer is "yes". Otherwise
extend an existing module.

## One-paragraph map

`graph.py` builds a `StateGraph` from a `GraphSpec` (preset YAML or in-code
`spec.py`). Each role node lives in `roles/`, takes `bindings: RoleBindings`,
and returns a partial `TaskState`. The graph is wrapped by
`AgentRoomService` (`service.py`) which exposes `run / stream / resume /
snapshot`. `cli.py` and `server/api.py` are two surfaces over the same
service. The thin UI under `server/ui/` is a third surface that consumes
the same HTTP endpoints. Tools live under `tools/`; context-management
engines under `context/`; cross-session memory under `memory/`; the LLM
transport seam under `llm/`.

For decision history, see [docs/adr/](adr/).
