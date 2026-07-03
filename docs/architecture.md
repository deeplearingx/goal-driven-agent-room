# architecture.md — agent-room 架构总览

> 配套：[CLAUDE.md](../CLAUDE.md)（项目宪法）、[PLAN.md](../PLAN.md)（动态路线图）、[ADR-0001](adr/0001-langgraph-as-orchestrator.md)、[ADR-0007](adr/0007-borrow-not-integrate-hermes.md)

## 1. 一张图

```mermaid
flowchart LR
    subgraph clients["调用方"]
        CLI["Typer CLI<br/>agent_room/cli.py"]
        HTTP["FastAPI + SSE<br/>agent_room/server/api.py"]
        Lib["Python 库<br/>AgentRoomService"]
    end

    Service["AgentRoomService<br/>agent_room/service.py<br/>run / stream / resume / snapshot"]

    subgraph graph["LangGraph StateGraph"]
        START((START))
        Planner["planner"]
        Developer["developer"]
        Reviewer["reviewer<br/>with_structured_output"]
        Delivery["delivery"]
        END((END))
    end

    subgraph state["TaskState (TypedDict)"]
        Fields["title / description / plan /<br/>code / review / delivery /<br/>revision_round / max_revisions /<br/>artifacts[+] / events[+] / status"]
    end

    subgraph persist["持久化层"]
        Saver["AsyncSqliteSaver +<br/>JsonPlusSerializer<br/>(allowed_msgpack_modules)"]
        SQLite[("agent_room.db<br/>(SQLite)")]
    end

    CLI --> Service
    HTTP --> Service
    Lib --> Service

    Service --> START
    START --> Planner
    Planner --> Developer
    Developer --> Reviewer
    Reviewer -->|approved| Delivery
    Reviewer -->|revision_required| Developer
    Reviewer -->|need_user_decision| END
    Delivery --> END

    graph -.每节点读写.-> state
    graph -.每步快照.-> Saver
    Saver --> SQLite
```

## 2. 模块边界

| 层 | 文件 | 职责 | 不该有什么 |
|----|------|------|-----------|
| **入口** | [cli.py](../agent_room/cli.py) / [server/api.py](../agent_room/server/api.py) | 解析参数 / HTTP 路由 / SSE 转发 | 业务逻辑、LLM 调用 |
| **服务门面** | [service.py](../agent_room/service.py) | task_id 管理、状态物化、resume 协议 | LangGraph 内部细节 |
| **编排** | [graph.py](../agent_room/graph.py) | StateGraph 装配、checkpointer 工厂 | 角色提示词 |
| **状态** | [state.py](../agent_room/state.py) / [schemas.py](../agent_room/schemas.py) | `TaskState` TypedDict、pydantic schema | 任何可变全局状态 |
| **角色** | [roles/](../agent_room/roles/) | 4 个节点函数（planner/developer/reviewer/delivery） | 跨节点直接调用 |
| **配置** | [config.py](../agent_room/config.py) | `RoleBindings`（LLM/工具注入点）、`Settings` | 硬编码 |
| **事件适配** | [events.py](../agent_room/events.py) | LangGraph 事件 → SSE 帧格式化 | 业务路由 |

## 3. 数据流（典型 happy path）

```
TaskRequest (title, description)
  └─▶ AgentRoomService.run(req)
        ├─ new_task_id() → "task-xxxxx..."
        ├─ thread_id = task_id  (LangGraph 复用 thread_id 做恢复键)
        ├─ initial TaskState
        └─▶ graph.ainvoke(state, config={"configurable": {"thread_id": ...}})
              ├─ planner   →  state.plan
              ├─ developer →  state.code, state.revision_round++
              ├─ reviewer  →  state.review : ReviewerDecision (structured)
              │                ├─ approved             → delivery
              │                ├─ revision_required    → developer 回环
              │                └─ need_user_decision   → halt（写入 checkpoint）
              └─ delivery  →  state.delivery, state.status="completed"
        └─▶ snapshot(task_id) → TaskResult
```

resume 路径：`service.resume(task_id, decision)` 调用 `graph.aupdate_state(..., as_node="reviewer")` 把 reviewer 决策强行改成 `revision_required + 用户指令`，再 `ainvoke(None)` 让图从中断点续跑。

## 4. 关键不变量

来自 [CLAUDE.md §3-§5](../CLAUDE.md)，全部依赖图本身的契约，没有运行时校验：

1. **单一状态源**：节点之间禁止共享变量、模块级 mutable、`Service` 实例字段。任何跨节点信息都通过 `TaskState` 的字段或 reducer 列表（`artifacts` / `events` / `user_directives` 用 `Annotated[list, add]` 累加）传递。
2. **状态派生（不存）**：任务运行状态（completed / awaiting_user / failed / running）由 [service._materialize_status](../agent_room/service.py) 从 `delivery` / `review.decision` / `revision_round` 派生。`TaskState` 不存 `status` / `error` 字段，避免双源 drift。
3. **Reviewer 协议**：`reviewer` 必须用 `with_structured_output(ReviewerDecision)`。三种 decision 是路由分叉的唯一信号，不允许 LLM 自由 JSON。
4. **Resume 协议**：`need_user_decision` 暂停后，`service.resume(task_id, decision)` 把用户指令追加进 `state.user_directives`（reducer list）；developer 节点显式读这个字段。**不**通过 `review.feedback` 偷渡用户输入，否则 reviewer prompt 改动会静默破坏 resume 语义。
5. **跨进程恢复**：所有需要"等用户"的暂停都走 LangGraph checkpointer。`need_user_decision` 后进程崩溃，重启后 `service.snapshot(task_id)` 必须返回完整状态。
6. **流式两层**：SSE 必须能区分节点级（`task_started` / `node_started` / `node_finished` / `task_finished`）和 token 级（`on_chat_model_stream`）。前者必出，后者按需。

## 5. 持久化与序列化

LangGraph 用 ormsgpack 把 checkpoint 写进 SQLite。
对自定义 pydantic 类型（`ReviewerDecision`、`Artifact`、`Event`）必须显式注册到 `allowed_msgpack_modules`，否则反序列化时会发警告（未来会硬阻断）：

```python
# agent_room/graph.py
_AGENT_ROOM_MSGPACK_ALLOWLIST = (ReviewerDecision, Artifact, Event)

def _agent_room_serde() -> JsonPlusSerializer:
    return JsonPlusSerializer(allowed_msgpack_modules=_AGENT_ROOM_MSGPACK_ALLOWLIST)

# 用 aiosqlite + AsyncSqliteSaver 直拼，from_conn_string 不接 serde 参数。
async with aiosqlite.connect(db_path) as conn:
    saver = AsyncSqliteSaver(conn, serde=_agent_room_serde())
    ...
```

## 6. 路线图占位

未来三个里程碑都接到 [graph.py](../agent_room/graph.py) 当前的扩展点上，**不**通过外部 agent 项目引入：

| 里程碑 | 接入点 | 自研模块（计划） |
|--------|--------|------------------|
| v0.2 DAG 配置化 | `build_uncompiled_graph` 改为接受 `GraphSpec` | `agent_room/spec.py` |
| v0.3 工具系统 | 角色子图 `agent ↔ tools`（替换当前 4 个纯函数节点） | `agent_room/tools/` |
| v0.4 上下文工程 | 节点调 LLM 前过 `ContextEngine` | `agent_room/context/` + `agent_room/prompt/` |
| v0.5 跨会话记忆 | `MemoryProvider` 注入 `RoleBindings`，节点开始 prefetch | `agent_room/memory/` |

每个模块都是「借鉴 [hermes-agent](../../hermes-agent/) 模式，本仓库自研」（见 [ADR-0007](adr/0007-borrow-not-integrate-hermes.md)）。

## 7. 测试拓扑

```
tests/
├─ fakes.py                       # 共享 FakeReviewerLLM / bindings_with_fakes
├─ test_graph.py                  # StateGraph + reducer + 路由
├─ test_sqlite_persistence.py     # 跨进程恢复（同一 thread_id, 重建 saver）
├─ test_api.py                    # FastAPI: ASGITransport + LifespanManager + 注入 service=
└─ test_cli.py                    # Typer CliRunner + monkeypatch RoleBindings 工厂
```

要点：API 测试通过 `create_app(service=...)` 注入预编译的图（绕过 SQLite）；
CLI 测试用 `monkeypatch.setattr("agent_room.cli.RoleBindings", _factory)` 把 LLM 工厂换成 fakes，DB 走 tmp 路径。
fake reviewer 的 `with_structured_output` memoize 一次后返回同一个 `FakeStructured`，让 decision 索引在 CLI 多次调用（每次都重建图）间保持单调。

---

最后修改：2026-06-11
