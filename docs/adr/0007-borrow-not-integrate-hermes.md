# ADR-0007：借鉴 hermes-agent 架构但不依赖它（独立性原则）

- 状态：accepted
- 日期：2026-06-11
- 关联：[CLAUDE.md §1.1](../../CLAUDE.md)、[PLAN.md §9 ADR-0007](../../PLAN.md)
- 取代：[ADR-0005](#背景：adr-0005-的废弃)（"不在本项目内做 RAG / 长期记忆"）

## 上下文

agent-room 的 v0.3-v0.5 路线（工具系统 / 上下文工程 / 跨会话记忆）需要的能力，
[hermes-agent](../../../hermes-agent/) 已经有成熟实现：

| 能力 | hermes-agent 实现位置 |
|------|----------------------|
| Tool Registry（自动发现 / `__slots__` / `RLock` / 大输出落盘） | `tools/registry.py` |
| ContextEngine（`should_compress` / 保护窗口 / 分级降级） | `agent/context_engine.py` + `agent/context_compressor.py` |
| 分层 prompt 装配（缓存友好不变量） | `agent/prompt_builder.py` |
| MemoryProvider（SQLite + FTS5 + truncate_around_matches） | `agent/memory_provider.py` + `tools/memory_tool.py` + `hermes_state.py` |

最初的方案（ADR-0005）是「这些能力暂不做，未来通过依赖 hermes-agent 引入」。
用户在 v0.1 收尾期间明确否决了这一思路，原话：
> 我让你分析 hermes 形成文档的原因就是借用它的架构。

## 决定

**借鉴 hermes-agent 的设计模式，在 agent-room 仓库内独立重新实现**。
hermes-agent 是只读参考资料，**不是**运行时依赖。

具体规则：
- ✅ 学习 hermes 的设计模式（接口形状、保护窗口策略、落盘策略、FTS5 截断算法等）。
- ✅ 在 [agent_room/](../../agent_room/) 内自研同等能力（命名 / 接口可以变）。
- ❌ **不** `from hermes_*` / `import hermes_*`。
- ❌ **不** 在 `pyproject.toml` 把 hermes-agent 列为依赖（含 optional-dependencies）。
- ❌ **不** 在 PLAN 里出现"集成 hermes"这类里程碑。
- ❌ **不** 提供"可选挂钩"绕过此原则。

## 验证（硬指标）

[PLAN.md §12.4](../../PLAN.md) 规定的独立性硬指标，全部具备自动化验证：

1. `agent_room/` 全文不出现 `from hermes_` / `import hermes_`：
   ```bash
   ! grep -RE "from hermes_|import hermes_" agent_room/
   ```
   `.pre-commit-config.yaml` local hook + `.github/workflows/ci.yml` 的 `independence`
   job 都执行这一条。

2. `pyproject.toml` 任何 dependency / optional-dependency 不引用 hermes-agent。
   CI `independence` job 第二步 grep。

3. **运行时硬测**：[`tests/test_independence.py`](../../tests/test_independence.py)
   在子进程里挂一个 `MetaPathFinder` 拦截任何 `hermes_*` 顶层导入，再
   `pkgutil.walk_packages` 强制导入 `agent_room` 全部 32 个子模块。任何懒加载
   / `importlib.import_module(...)` 计算名 / 经 `__getattr__` 触发的间接引用
   都会立即报错。这等价于"删除 `hermes-agent` 目录后再跑 `pytest -q` 仍全绿"——
   但比纯粹删除目录更严：删目录只验证模块**找不到**的场景，运行时挂阻断器额外
   验证「就算系统装了 hermes-agent，agent_room 也不会偷偷碰它」。CI `independence`
   job 第三步具名跑此测试。

第 3 条是终极判据：在 hermes 不可用 / 即使在场也不许碰的双重约束下本项目仍正常
工作，才算独立。

## 后果

**好处**：
- agent-room 可独立分发到无 hermes-agent 的环境（CI、容器、外部部署）。
- 不被 hermes-agent 的版本升级 / 接口变更绑架。
- 借鉴而非复用，强制每个借来的模式都通过本项目的代码评审，避免抄进不合身的复杂度。

**代价**：
- v0.3-v0.5 三个里程碑要重写已有功能，工期成本上升（[PLAN.md §3-§5](../../PLAN.md)）。
- 存在两类回归风险：
  - **[RISK-7](../../PLAN.md)**：抄代码而非借鉴模式，独立性被污染。缓解：code review + CI grep 断言。
  - **[RISK-8](../../PLAN.md)**：自研版功能比 hermes 退化。缓解：每个版本前先写"hermes 行为对照清单"，关键场景必须有测试。

## 背景：ADR-0005 的废弃

ADR-0005 原文："不在本项目内做 RAG / 长期记忆"，理由是"避免重复劳动，等需要时引入 hermes"。
该理由已与本 ADR 冲突，标记为 **superseded**。
v0.5 在 PLAN 中重新立项，定位是「跨会话记忆 + FTS5，借鉴 hermes 模式自研」。

## 相关决策

- [ADR-0001](0001-langgraph-as-orchestrator.md)：选择 LangGraph 作为编排器
- ADR-0002：Reviewer 协议用 `with_structured_output`（待写）
- ADR-0003：v0.2 GraphSpec YAML 配置（待写）
