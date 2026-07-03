# CLAUDE.md — agent-room 项目宪法

> 配套：[PLAN.md](PLAN.md) 路线图 / [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)
> 目录结构 / [CONTRIBUTING.md](CONTRIBUTING.md) 协作流程 / [SECURITY.md](SECURITY.md)
> 安全约定。本文件可变得很慢，跨 3 文件的改动 / 新依赖 / 改公开 API 都先在
> PLAN 立项。

---

## 1. Mission

> **独立、自洽**地把"多角色 LLM 协作 + 状态机 + 持久化 + 流式 + 人工介入 +
> 工具调用 + 上下文/记忆管理"做到生产可用，**用最少的代码**。

硬目标：

1. **独立性**：零依赖外部 agent 项目（hermes-agent 等）。所有能力在
   [agent_room/](agent_room/) 内自洽实现。
2. **可用性**：`pip install -e .` 后 30 行 Python 跑通完整流水线。
3. **可观测性**：每节点 start/end、token、reviewer 决策、状态机分叉都从
   SSE 流出。
4. **可恢复性**：进程崩溃后 `service.snapshot(task_id)` 拿回完整状态；
   `need_user_decision` 暂停后能 `resume()` 续跑。

### 1.1 与 hermes-agent 的关系

[hermes-agent](../hermes-agent/) 是**参考架构**，不是依赖。

- ✅ 学设计模式（Tool Registry、ContextEngine、MemoryProvider、prompt_builder
  分层、tool_result_storage 落盘、FTS5 检索…）。
- ✅ 把好模式**重新实现**进 agent-room（命名/接口可变，思想吸收）。
- ❌ **不** import hermes-agent 包；不在 PLAN 里写"集成 hermes"；不挂可选依赖。

判定：删除 `/home/ly/hermes-agent/` 后 `pytest -q` 必须仍全绿。运行测试见
[`tests/_independence_driver.py`](tests/_independence_driver.py)。

### 1.2 非目标 (Non-goals)

- ❌ 不做"通用 agent 框架"。只服务**多角色协作 + 状态机**这一种形态。
- ❌ 不做 UI 框架。仓内薄 UI 是**开发/演示** surface（[ADR-0012](docs/adr/0012-in-repo-thin-ui.md)），
  生产用户用 OpenAPI 自建。
- ❌ 不依赖任何外部 agent 项目（见 §1.1）。

### 1.3 目标内（v1.0 新增）

**轻量 LLM provider 抽象**（[ADR-0013](docs/adr/0013-llm-transport-abstraction.md)）：在 LangChain
之上薄 transport 抽象（`Transport` + `NormalizedResponse`），借鉴 hermes
`agent/transports/` 的 `convert_messages → convert_tools → build_kwargs →
normalize_response` 模式。LangChain 仍是默认运行时；transport 是 reviewer
协议、prompt cache、parser-error retry 的统一插入点。**规模上限：≤ 3 文件、
≤ 300 行、≤ 2 实现**。超出即违规，需另立项。

---

## 2. 技术栈与禁止

| 类别 | 选择 | 不可替换原因 |
|-----|------|------------|
| 编排 | **LangGraph** ≥ 0.2.50 | StateGraph + Checkpointer + `astream_events` 是核心 |
| LLM 接口 | **langchain-core** ≥ 0.3.20 | `with_structured_output` 是 reviewer 协议基础 |
| 数据校验 | **Pydantic v2** ≥ 2.9 | LangChain v0.3 已切到 v2 |
| HTTP | **FastAPI** + **sse-starlette** | 与 `astream_events` 异步生成器对接 |
| 持久化 | **AsyncSqliteSaver**（LangGraph 内置） | 不引入额外数据库 |
| CLI | **Typer** + **Rich** | 与 FastAPI 生态一致，类型友好 |
| Python | **≥ 3.11** | `TypedDict` Annotated、`asyncio.TaskGroup` |

**禁止**：自己写 SSE 协议；引入 SQLAlchemy；不经过 transport 直接调
`BaseChatModel`（[ADR-0013](docs/adr/0013-llm-transport-abstraction.md)）。

**安全 / 隐私四条**：API key 只读 `.env`/env 永不入仓库；SQLite DB 不跨租户；
SSE 不做敏感过滤（调用方负责）；详见 [SECURITY.md](SECURITY.md)。

---

## 3. 代码规范

通用规范走 `ruff format` + `ruff check`（行宽 100、import 排序、`X | None`
而非 `Optional[X]`）；Python 类型标注**必须有**。具体配置见
[`pyproject.toml`](pyproject.toml)。下面只列项目个性化的部分。

### 3.1 数据模型选择

- 跨节点状态用 `TypedDict`（LangGraph 要求）
- 外部 IO（HTTP body、CLI 入参、API 返回）用 **Pydantic BaseModel**
- 内部值对象用 **frozen dataclass** 或 Pydantic（看是否需要校验）
- **永远**在节点之间传结构化对象，不传 dict 字面量

### 3.2 异步

所有节点 `async def`；用 `ainvoke` / `astream_events` 不混用同步 API；资源
（checkpointer、HTTP client）用 `async with` 管生命周期。

### 3.3 注释

- **默认不写注释**。命名好就够。
- 仅在两种场景写：(a) 解释**为什么**这么写（LangGraph quirk 这类），
  (b) 标注**外部约束**（reviewer 协议字段顺序绑定前端契约）。
- 禁止 `# TODO` 留主分支。要么做掉，要么写到 [PLAN.md](PLAN.md)。

### 3.4 错误处理

- **节点函数内**不捕 LLM 异常 —— 让 LangGraph 沉淀到 checkpoint。
- **服务边界**（FastAPI handler、CLI 命令）必须捕获并转译用户错误。
- 不允许吞异常。`except: pass` review 必拒。

### 3.5 日志

v0.x 用 `print` / `rich.console`；v1.0 切 `structlog`，结构化字段
`task_id` / `role` / `round` / `event`。

---

## 4. 架构铁律 (Invariants)

不是建议，是规则。违反必须先在 [PLAN.md](PLAN.md) 立项。

### 4.1 单一状态源

**所有状态都在 `TaskState` 里。** 节点不持有可变状态，不写全局变量。

```python
# ✅ 对
async def developer(state: TaskState) -> dict:
    return {"code": ...}

# ❌ 错
_last_code = None
async def developer(state):
    global _last_code; _last_code = ...
```

### 4.2 节点是纯函数 (业务上)

节点只能：(1) 读 `state` (2) 调 LLM/工具 (3) 返回 partial state dict。

**禁止**：直接写 SQLite（让 checkpointer 做）；调 FastAPI（节点不知道 HTTP）；
打日志业务事件（用 `events` 字段累积）。

### 4.3 Reviewer 协议是契约

`ReviewerDecision` 三个 decision 值（`approved` / `revision_required` /
`need_user_decision`）是**跨语言契约**。修改它需要：(1) 在 PLAN.md 写 ADR
(2) 同步前端 / consumer 类型。

### 4.4 Checkpointer 不可绕过

不允许节点里手写 SQLite 持久化。所有"任务运行历史"都通过
`graph.aget_state(config)` 取得。

### 4.5 角色绑定通过 RoleBindings + Transport

不允许节点里 `ChatAnthropic(...)` 写死。全部走 `bindings.resolve(role)`，
fallback 链才生效。**业务代码读 `transport.invoke(role, ...)`**
（[ADR-0013](docs/adr/0013-llm-transport-abstraction.md)），transport 内部代理 `bindings.resolve`。

### 4.6 事件流是只读视图

`astream_events` 是给前端看的。后端逻辑判断必须从 `state` 读，不准从事件
流"重建"状态。

---

## 5. 测试要求

### 5.1 必须

- 节点逻辑要有**离线测试**（用 `FakeListChatModel` + `FakeStructured`）。
- `pytest -q` < 5 秒、零网络、零外部依赖。
- 每次改 `graph.py` 的边或路由函数，必须新增覆盖该路径的测试。

### 5.2 失败状态机覆盖

至少要有这 4 个场景的测试（已实现）：(1) happy path（首次 approved）;
(2) revision_required → 修订 → approved; (3) revision_required 用尽 → failed;
(4) need_user_decision → resume → completed。

E2E（真实 API key）放 `tests/integration/`，CI 默认跳过。

---

## 6. 与 AI 助手协作的约定

### 6.1 行为规则

1. **优先读** CLAUDE.md + PLAN.md，再读代码。
2. **改动前**先在 todo 列计划；超过 3 文件先 PLAN.md 立项。
3. **不要**主动加 logging / `try/except` / docstring 段落。
4. **不要**把"future-proofing / 灵活性 / 可扩展性"当理由加抽象层。三个具体
   例子才证明需要抽象。
5. **不要**用注释解释代码做什么，命名好就够。
6. 修 bug 优先找**根因**，不打补丁绕过。
7. 改完代码必须 `pytest -q`，没绿不算改完。

### 6.2 推荐做法

- 写代码前先 grep 已有实现，避免重复。
- 节点改了之后先想"`TaskState` 是否需要新字段"。
- 写测试先写**反向用例**（错误路径 / 边界），再写 happy path。
- 对外 API（`AgentRoomService` / `schemas.py` / FastAPI 端点）改名属于破坏性
  变更，需要 PLAN.md ADR。

### 6.3 不应该做

- ❌ 引入 `langchain` 整包（已有 `langchain-core` 就够；provider 单独装）
- ❌ 节点里搞重试（重试是 LangGraph 节点级 retry policy 的工作）
- ❌ 状态机改成事件驱动（违反 4.1 / 4.2）
- ❌ `service.py` 里做业务逻辑（service 是门面，业务在 roles 和 graph）
- ❌ 给一次性脚本写完整测试（examples/ 不强制覆盖）

### 6.4 文档维护优先级

CLAUDE.md > PLAN.md > 代码现状。CLAUDE.md 慎改，每次改 PR 里说明；
PLAN.md 动态路线图，每完成一个里程碑归档；docs/adr/ 一个 ADR 一文件，
命名 `NNNN-title.md`。

---

最后修改：2026-06-23（v1.0 trim — see PLAN.md §0 ledger for migration map to
docs/ARCHITECTURE.md / CONTRIBUTING.md / SECURITY.md）
