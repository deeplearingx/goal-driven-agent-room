# agent-room

[![CI](https://github.com/agent-room/agent-room-py/actions/workflows/ci.yml/badge.svg)](https://github.com/agent-room/agent-room-py/actions/workflows/ci.yml)
[![coverage](https://img.shields.io/badge/coverage-93%25-brightgreen.svg)](#coverage)
[![python](https://img.shields.io/badge/python-3.11%20%7C%203.12%20%7C%203.13-blue.svg)](pyproject.toml)

A **multi-agent collaboration** system built on **LangGraph** — multiple
specialized roles (planner → developer → reviewer → delivery) working a task
together. Python rewrite of the TypeScript `agent-room` from `hermes-web-ui`,
distilled into ~600 lines of code.

> **Which kind of "multi-agent"?** The *centralized, controllable, evaluable*
> kind: one tool-using autonomous agent (the developer's ReAct loop) plus three
> LLM roles, coordinated by a state machine — **not** a decentralized swarm
> (no A2A, no dynamic sub-agent trees, no parallel autonomous agents). That
> choice is deliberate: centralized control is what makes every step traceable
> (OpenTelemetry) and every design decision ablation-testable (see
> [docs/portfolio.md](docs/portfolio.md)).

> **📋 工程笔记 / 作品集**: [docs/portfolio.md](docs/portfolio.md) ——
> 设计决策 + 支撑它们的 eval 数字（planner-gate 消融 25%→100%、harness 抓到的一个
> 健壮性 bug，以及为什么）。
>
> **Project conventions**: see [CLAUDE.md](CLAUDE.md) (constitution),
> [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) (layout),
> [CONTRIBUTING.md](CONTRIBUTING.md) (workflow), and
> [SECURITY.md](SECURITY.md) (security).
> **Roadmap & ADR index**: see [PLAN.md](PLAN.md).

```
   ┌──────────┐    ┌────────────┐    ┌──────────┐
   │ planner  ├───▶│ developer  ├───▶│ reviewer │
   └──────────┘    └────────────┘    └────┬─────┘
                         ▲                 │
                         │ revision_required
                         └─────────────────┤
                                           │ approved
                                           ▼
                                      ┌──────────┐
                                      │ delivery │
                                      └──────────┘
```

## Features

- **Strict reviewer protocol** via `with_structured_output(ReviewerDecision)` —
  no fragile JSON parsing.
- **SQLite-backed checkpoints** with `AsyncSqliteSaver` — every node write is
  durable; crashes resume from the last node.
- **Native streaming** via `astream_events` → SSE — no 3 s polling.
- **Human-in-the-loop**: `need_user_decision` halts the run; `service.resume()`
  patches state and continues.
- **Pluggable role bindings**: each role can use a different model (e.g. Sonnet
  for planner, Opus for developer).
- **Three surfaces**: Python API, Typer CLI, FastAPI/SSE server, plus an
  in-repo thin UI at `/ui` (Jinja2 + single `app.js`, no npm) — see
  [ADR-0012](docs/adr/0012-in-repo-thin-ui.md) and
  [examples/ui_smoke.md](examples/ui_smoke.md).
- **OpenTelemetry tracing**: every task emits a standard `task → node → llm`
  span tree (GenAI semantic conventions). Zero-impact by default (no-op until a
  provider is configured); `AGENT_ROOM_TRACE=console` for local spans.
- **MCP tools** (optional, `pip install 'agent-room[mcp]'`): load any
  [Model Context Protocol](https://modelcontextprotocol.io) server's tools into
  the registry — any role uses them like built-ins.
- **Ablation eval harness**: toggle each design choice (planner-gate / context
  engine / memory / reviewer) and measure its task-success Δ — see
  [docs/portfolio.md](docs/portfolio.md).

## Install

> **Production/high-concurrency deployment:** the Go admission gateway,
> RabbitMQ work queues, PostgreSQL checkpoints, transactional outbox, replayable
> SSE and horizontally-scaled Python workers are documented in
> [docs/production-go-rabbitmq.md](docs/production-go-rabbitmq.md). The original
> FastAPI + SQLite path below remains the lightweight local/development mode.
> Go integrations and the `kbctl` operator CLI are documented in
> [docs/go-sdk-cli.md](docs/go-sdk-cli.md).
> Reliable outbound Webhook and Feishu notifications are documented in
> [docs/webhook-feishu.md](docs/webhook-feishu.md).
> Redis-backed configuration caching and multi-instance invalidation are
> documented in [docs/redis-config-cache.md](docs/redis-config-cache.md).

```bash
cd /home/ly/agent-room-py
python -m venv .venv && source .venv/bin/activate
pip install -e '.[dev]'
cp .env.example .env  # set ANTHROPIC_API_KEY
```

## Use

### Python

```python
import asyncio
from agent_room import RoleBindings, TaskRequest, build_agent_room_graph
from agent_room.service import AgentRoomService

async def main():
    bindings = RoleBindings()  # uses env-driven defaults
    graph = build_agent_room_graph(bindings)
    service = AgentRoomService(graph)
    result = await service.run(
        TaskRequest(title="FizzBuzz", description="Implement FizzBuzz with tests")
    )
    print(result.delivery)

asyncio.run(main())
```

### CLI

```bash
agent-room run "FizzBuzz" -d "Implement FizzBuzz with tests" --stream
agent-room show task-abc123
agent-room resume task-abc123 -d "use library A"
agent-room serve --port 8765
```

### HTTP

```bash
# blocking run
curl -sX POST localhost:8765/tasks \
  -H 'content-type: application/json' \
  -d '{"title":"x","description":"y"}'

# streaming
curl -N -X POST localhost:8765/tasks/stream \
  -H 'content-type: application/json' \
  -d '{"title":"x","description":"y"}'

# resume
curl -sX POST localhost:8765/tasks/<id>/resume \
  -H 'content-type: application/json' \
  -d '{"decision":"use library A"}'
```

### Browser (in-repo thin UI)

`agent-room serve` mounts a Jinja2-based UI at `/ui` (root `/` redirects there
when the pixel app isn't built). Open <http://localhost:8765/>, fill the
new-task form, and watch SSE events live; if the reviewer escalates, a resume
textarea appears in-place. No npm, no build step. Production users can target
the OpenAPI surface (`/docs`) directly. Walkthrough:
[examples/ui_smoke.md](examples/ui_smoke.md).

### Browser (pixel multi-agent UI) — one command

The React pixel UI (`mulit_agent_web_ui`) is served same-origin by the backend
at `/app` once built, so the whole demo runs from one process:

```bash
make demo          # builds mulit_agent_web_ui/dist, then `agent-room serve`
# → open http://localhost:8765/app  (root `/` redirects here when built)
```

No second server, no CORS — the SPA calls `/tasks/stream` on the same origin.
While editing the frontend, use `cd mulit_agent_web_ui && npm run dev` for hot
reload (Vite proxies the API to `:8765`).

## Layout

```
agent_room/
  schemas.py       # TaskRequest, ReviewerDecision, Artifact, Event, TaskResult
  state.py         # TaskState (TypedDict + reducer-annotated lists)
  config.py        # Settings + RoleBindings (env fallback chain)
  roles/
    planner.py     # plan generation
    developer.py   # code generation, feedback-aware
    reviewer.py    # ReviewerDecision via structured output
    delivery.py    # final handoff doc
  graph.py         # StateGraph wiring + SqliteSaver context manager
  service.py       # AgentRoomService façade (run/stream/resume/snapshot)
  events.py        # astream_events → UI-friendly dicts
  cli.py           # `agent-room` Typer entrypoint
  server/api.py    # FastAPI + SSE
tests/             # offline tests with FakeListChatModel
examples/          # runnable PoCs
```

## Test

```bash
pytest -q          # offline; no API key needed
```

## Coverage

```bash
# terminal report (current floor: 90%, baseline: 93%)
pytest -q --cov=agent_room --cov-report=term-missing

# HTML report at htmlcov/index.html
pytest -q --cov=agent_room --cov-report=html

# XML for CI ingest (codecov, sonar, …)
pytest -q --cov=agent_room --cov-report=xml
```

CI uploads `coverage.xml` + `htmlcov/` as a build artifact (`coverage-report`,
14-day retention). Threshold is enforced by `[tool.coverage.report] fail_under = 90`
in [pyproject.toml](pyproject.toml). Bump it back up once new modules stabilise.

## Mapping to the original TS

| TS `agent-room` | Here |
|-----------------|------|
| `runner/runtime/orchestrated-gateway-runtime.ts` | [`agent_room/graph.py`](agent_room/graph.py) |
| `services/hermes/agent-room/index.ts` (service layer) | [`agent_room/service.py`](agent_room/service.py) |
| `parseReviewerJson` | `with_structured_output(ReviewerDecision)` in [`roles/reviewer.py`](agent_room/roles/reviewer.py) |
| `applyRunnerResult` (SQLite txn) | LangGraph checkpointer — no manual code needed |
| `event-adapter.ts` | [`agent_room/events.py`](agent_room/events.py) |
| 3 s polling client | SSE via `astream_events` |
| Profile fallback chain | [`agent_room/config.py`](agent_room/config.py) `RoleBindings.resolve` |

## Roadmap

详见 [PLAN.md](PLAN.md)。要点：

- **v0.2** — DAG 配置化（YAML → `StateGraph`），不再写死 4 节点
- **v0.3** — 工具系统（自研 Tool Registry / 权限 / 大输出落盘，模式借鉴 hermes-agent）
- **v0.4** — 上下文工程（自研 ContextEngine + 分层 prompt 装配，模式借鉴 hermes-agent）
- **v0.5** — 跨会话记忆（自研 SQLite + FTS5 MemoryProvider，模式借鉴 hermes-agent）
- **v1.0** — 重定向为**作品集证明**：消融 eval harness（4 轴真数据）+ 仓内薄 UI +
  LLM transport 抽象 + OpenTelemetry 追踪 + MCP 工具。详见 [PLAN.md](PLAN.md) 北极星节
  与 [docs/portfolio.md](docs/portfolio.md)

> **独立性承诺**：本项目不依赖 hermes-agent。删除 `/home/ly/hermes-agent/` 后所有功能正常。
> hermes-agent 是参考架构，不是 runtime 依赖。详见 [CLAUDE.md §1.1](CLAUDE.md)。
