# ADR-0001：用 LangGraph 作为编排器

- 状态：accepted
- 日期：2026-06-11
- 关联：[CLAUDE.md §2 技术栈](../../CLAUDE.md)、[PLAN.md §9 决策日志](../../PLAN.md)

## 上下文

agent-room 的核心形态是「多角色 LLM 协作 + 状态机 + 持久化 + 流式 + 人工介入 + 工具调用 + 上下文/记忆管理」。
要在 Python 生态里挑一个底座，绕不开三类候选：

| 候选 | 一句话定位 |
|------|------------|
| **LangGraph** | LangChain 出的状态图编排器，原生 checkpointer + 流式事件 |
| **CrewAI** | 任务/角色高阶 DSL，强调"团队"语义 |
| **AutoGen** | 多 agent 对话（Conversable Agent）框架，群聊式协作 |
| **自研** | 直接在 asyncio 上拼一个状态机 + SSE |

agent-room 的硬约束（来自 [CLAUDE.md §1](../../CLAUDE.md)）：
1. 节点之间状态必须严格走单一 TypedDict，不允许"对象互发消息"。
2. 跨进程恢复（reviewer `need_user_decision` → 等用户 → resume）必须开箱可用。
3. Reviewer 的判定必须是强 schema（approved / revision_required / need_user_decision），不能是 LLM 自由发挥的 JSON。
4. 流式必须能拿到 token 级和节点级两层事件，前端要能渲染轨迹。

## 决定

选 **LangGraph**（≥ 0.2.50）作为编排底座。

具体到本项目用到的能力：
- `StateGraph` + `TypedDict` 状态：单一状态源，节点纯函数读写。
- `add_conditional_edges`：reviewer 决策分叉（approved → delivery、revision_required → developer、need_user_decision → halt）。
- `AsyncSqliteSaver` checkpointer：跨进程恢复，无需自建表。
- `astream_events`：原生节点开始/结束 + token 事件，喂给 sse-starlette 直出。
- `with_structured_output`（来自 langchain-core）：reviewer 协议用 pydantic schema 强约束。

## 备选与拒绝原因

**CrewAI**：高阶 DSL 把"任务/团队/委派"当一等公民，但这恰好不是本项目要的形态。
本项目要的是显式状态机控制流，CrewAI 的隐式调度反而是阻碍。
checkpointer / 流式 / structured output 都得自己补。

**AutoGen**：群聊式协作，每个 agent 是 ConversableAgent。
对"planner→developer→reviewer→delivery 严格四步 + 显式回环"这种确定性 DAG 来说过重，
状态散在多个 agent 的对话历史里，恢复语义不清。

**自研**：可行但要重新实现 checkpointer、流式事件协议、structured output 三件套，
跟 LangGraph 已经免费提供的能力是直接重复劳动。
本项目的差异化不在编排器本身，而在「多角色协作 + 工具/上下文/记忆借鉴 hermes 模式自研」。

## 后果

**好处**：
- v0.1 在 ~600 行业务代码内跑通完整流水线，编排相关代码 < 100 行。
- checkpointer / SSE / structured output 都是 LangGraph 内建，省掉至少 1-2 周自研。

**代价**：
- 锁定 LangGraph 大版本（[RISK-1](../../PLAN.md)）。0.2 → 0.3 已有过 breaking change，需要 pin 版本范围 + 跟 changelog。
- LangGraph 的状态 reducer / 通道概念有学习成本，新人上手要先理解 `Annotated[list, add]` 的语义。
- v0.2 配置化要在 LangGraph 的 `add_conditional_edges` 之上做一层 GraphSpec → StateGraph 的映射（[PLAN.md §2](../../PLAN.md)）。

## 相关决策

- [ADR-0002](0002-reviewer-protocol.md)：reviewer 用 `with_structured_output`（已记录在 PLAN §9，正文待写）
- [ADR-0007](0007-borrow-not-integrate-hermes.md)：独立性原则
- 工具/上下文/记忆三层（v0.3-v0.5）一律「借鉴 hermes 模式，本仓库自研」，**不**通过 LangGraph 之外的第三方 agent 框架引入。
