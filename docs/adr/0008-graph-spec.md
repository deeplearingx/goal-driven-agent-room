# ADR-0008：v0.2 GraphSpec — DAG 配置化

- 状态：accepted (2026-06-11 — 实现完成，53/53 测试绿，ruff 干净)
- 日期：2026-06-11
- 关联：[CLAUDE.md §3](../../CLAUDE.md)、[ADR-0001](0001-langgraph-as-orchestrator.md)、[ADR-0002](0002-reviewer-protocol.md)、[ADR-0007](0007-borrow-not-integrate-hermes.md)、[PLAN.md §2](../../PLAN.md)
- 取代：无；为 v0.2 主要工作
- 实现：[`agent_room/spec.py`](../../agent_room/spec.py)、[`agent_room/routers.py`](../../agent_room/routers.py)、[`agent_room/graph.py::build_from_spec`](../../agent_room/graph.py)、[`agent_room/presets/`](../../agent_room/presets/)、用户文档 [`docs/graph-spec.md`](../graph-spec.md)、20 个回归测试 [`tests/test_spec.py`](../../tests/test_spec.py)

## 上下文

v0.1 把 4 角色流水线（planner → developer → reviewer → delivery）写死在
[graph.py::build_uncompiled_graph](../../agent_room/graph.py)。

实战里有更多形态：

| 形态 | 节点 | 边 |
|---|---|---|
| 单角色直答 | `solo` | `solo → END` |
| 双角色 dev+review | `developer / reviewer` | `dev ↔ review`，approved → END |
| 多评审 fan-out | `dev / sec_review / perf_review / delivery` | `dev → {sec, perf}`（并行）→ `aggregate` → `delivery` |
| 工具增强 dev | `dev` 内嵌 `ToolNode` 自循环 | v0.3 引入 |

写死等于让用户改 Python。我们想要：写一个 30 行 YAML 描述任意 DAG。

**约束**（来自 v0.1 实战 + 烟雾测试）：

1. **路由信号必须可静态分析**（继承 ADR-0002）。每条 conditional edge 的分支映射在 spec 里**必须显式枚举**，不允许"动态返回 next 节点名"，否则 graph 无法在加载时校验。
2. **状态字段不变**。v0.2 沿用 `TaskState`，不开放自定义字段；v0.3 再考虑 generic state。让用户在 v0.2 同时担心拓扑+状态会暴增配置面。
3. **YAML 表达力天花板要有出口**。复杂 router 应允许指向 Python callable（`router: my.module.fn`），类似 hermes-agent tools registry 的 dotted-path 风格。
4. **F2 的发现要落到 spec 模型**：reviewer 在 v0.1 烟雾测试中没有看到 `user_directives`，因为 prompt 是硬编码的。GraphSpec 必须支持 **per-node prompt override**（system prompt + 模板变量），否则同样的角色在不同流水线里无法定制。

## 决定

引入 `agent_room/spec.py`（Pydantic v2 模型 + YAML loader），加 `build_from_spec(spec, bindings)`。

### 1. Schema（`agent_room/spec.py`）

```python
from typing import Literal, Annotated
from pydantic import BaseModel, Field, model_validator

RoleName = Literal["planner", "developer", "reviewer", "delivery"]
# v0.2 锁定四个内置角色 — 加新角色 = ADR 修订 + 实现新 role node + 改 TaskState
# 这刻意保守。v0.3 才允许用户注册自定义角色。

ReservedNode = Literal["__entry__", "__end__"]

class NodeSpec(BaseModel):
    """One node in the graph. `role` picks which role implementation to instantiate."""
    role: RoleName
    model: str | None = None              # 覆盖 settings 默认；None 走全局
    prompt_override: str | None = None    # 覆盖角色的 SYSTEM prompt
    extra_context_keys: list[str] = []    # 把 state 里这些 key 拼进 HUMAN message
    # （v0.3 加 tools: list[str]，dotted paths 到 BaseTool 实例）

class BranchSpec(BaseModel):
    """Map a router signal to a destination node name."""
    on: str                                # e.g. "approved", "_exhausted"
    to: str                                # node name OR "__end__"

class EdgeSpec(BaseModel):
    """Either an unconditional edge OR a conditional one with branches."""
    from_: str = Field(alias="from")
    to: str | None = None                  # set for unconditional
    branches: list[BranchSpec] | None = None
    router: str | None = None              # dotted path to callable; defaults to built-in
                                           # `agent_room.routers.review_router` for reviewer

    @model_validator(mode="after")
    def _exactly_one_path(self):
        has_to = self.to is not None
        has_branches = bool(self.branches)
        if has_to == has_branches:
            raise ValueError("EdgeSpec needs exactly one of `to` or `branches`")
        return self

class GraphSpec(BaseModel):
    name: str
    entry: str                             # node name
    nodes: dict[str, NodeSpec]
    edges: list[EdgeSpec]

    @model_validator(mode="after")
    def _references_resolve(self):
        names = set(self.nodes) | {"__end__"}
        if self.entry not in self.nodes:
            raise ValueError(f"entry={self.entry!r} not in nodes")
        for e in self.edges:
            if e.from_ not in self.nodes:
                raise ValueError(f"edge.from={e.from_!r} not a node")
            targets = [e.to] if e.to else [b.to for b in (e.branches or [])]
            for t in targets:
                if t not in names:
                    raise ValueError(f"edge target {t!r} not a node and not __end__")
        return self
```

**说明 / 取舍**：

- `RoleName` 是 `Literal`，加角色必须改 schema。比 `str` 多一道编译期检查。
- `EdgeSpec` 用 discriminated-style validator 强制 `to` 和 `branches` 二选一。
- `prompt_override` 直接换掉 SYSTEM prompt——这是 F2 的需要。`extra_context_keys` 把 state 字段拼进 HUMAN message（解决 v0.1 reviewer 没看到 directives 的 bug 类型）。
- 不引入 "node groups"、"sub-graphs"、"loops with break condition" 等高级概念。让 v0.2 stay boring；这些 v0.3+ 再加。

### 2. Loader（`agent_room/spec.py`）

```python
def load_graph_spec(source: str | Path | dict) -> GraphSpec: ...
```

接受 path / 字符串 / dict 三种输入。Path 走 `yaml.safe_load`。**禁止 `yaml.load`**（任意类对象注入风险）。

### 3. Builder（`agent_room/graph.py`）

```python
def build_from_spec(
    spec: GraphSpec,
    bindings: RoleBindings,
    *,
    checkpointer: BaseCheckpointSaver | None = None,
) -> CompiledStateGraph: ...
```

行为：

1. 对每个 `NodeSpec`，按 `role` 派发到 `make_planner / make_developer / make_reviewer / make_delivery`，并把 `prompt_override` / `extra_context_keys` / `model` 传进去（要求各 role factory 接受这些 kwargs；这是 v0.2 配套改动）。
2. 对每个 `EdgeSpec`：
   - 无条件边 → `graph.add_edge(from, to)`
   - 有 `branches` → 解析 `router`（默认 `review_router` 看 `state.review.decision`），然后 `graph.add_conditional_edges(from, router_fn, mapping)`，mapping 把 spec 里的字符串 key 映射到节点名。
3. `entry` → `graph.set_entry_point`
4. 编译时传入 checkpointer（沿用 [build_with_sqlite_checkpointer](../../agent_room/graph.py) 的模式）。

**老 API 兼容**：保留 [build_agent_room_graph](../../agent_room/graph.py)；它在内部加载 `presets/full.yaml` 通过 `build_from_spec` 跑出来。32 个老测试一行不改。

### 4. 预设（`agent_room/presets/`）

| 名 | 文件 | 拓扑 |
|---|---|---|
| `full` | `full.yaml` | 当前 4 角色流水线，`build_agent_room_graph` 默认值 |
| `dev_review` | `dev_review.yaml` | `dev ↔ review`（无 planner / delivery） |
| `solo` | `solo.yaml` | 单 `developer` 节点 → END |

CLI 加 `--graph <preset|path>`：preset 名直接查 `presets/`，否则按文件路径加载。

### 5. Router 解析

默认 router 是内置的 `agent_room.routers.review_router`：读 `state.review.decision`，如果 `revision_round > max_revisions` 就走 `_exhausted`。这是 v0.1 [graph._route_after_review](../../agent_room/graph.py) 的迁移版本（出口标准：行为完全等价）。

用户可以在 `EdgeSpec.router` 写 `"my_pkg.my_module:my_router"`，签名 `(state: TaskState) -> str`。Loader 用 `importlib` 解析；解析失败立刻抛错（不静默降级）。

## 备选方案

### A. Python DSL 而不是 YAML

```python
graph = (
    GraphBuilder()
    .add(planner)
    .then(developer)
    .branch(reviewer, approved=delivery, revision_required=developer)
    .build()
)
```

**否决理由**：表达力差不多，但失去"配置可序列化、可跨语言读取、可被产品/PM 改"的好处。本项目的目标读者就包括非 Python 工程师调拓扑。Python DSL 留作 v0.3 给"用 Python 注册自定义节点"的人用。

### B. 完全照抄 LangGraph 的 `StateGraph` API，让用户直接写 Python

最简单，但是把 v0.1 [build_uncompiled_graph](../../agent_room/graph.py) 的"硬编码"问题原样转嫁给用户。每改个流水线就要改一行 Python + 重启。pass。

### C. 让 `EdgeSpec.branches` 用 dict 而不是 list

```yaml
edges:
  - from: reviewer
    branches: {approved: delivery, revision_required: developer}
```

更紧凑，但失去顺序保证（YAML 里 dict 顺序在 Python 3.7+ 是稳定的，但 spec validation 报错时定位差）。**取**：保持 list，每条 branch 一行 `on/to`，校验错误能给精确行号。

### D. 把 reviewer 的 `decision` enum 也写进 spec

让用户能加自定义 decision（比如 `escalate_to_pm`）。

**否决理由**：[ADR-0002](0002-reviewer-protocol.md) 明确说 `ReviewDecisionLiteral` 是 schema 硬合约。改 enum = 改 [schemas.py](../../agent_room/schemas.py) + ADR 修订。spec 不能绕过这个合约。spec 只能把 enum 已有的值映射到不同节点。

## 后果

**好处**：

- v0.1 烟雾测试发现的 F2（reviewer 看不到 directives）有了**配置驱动的修法**：在 spec 里给 reviewer 节点加 `extra_context_keys: [user_directives, plan, code, title, description]`，不改代码就能加新字段（前提是新字段已经在 TaskState 里）。
- 老 API 100% 兼容，老测试零改动。
- 加新 preset = 加 YAML，CI 跑 spec validator + 1 个集成测试就稳。

**代价**：

- 4 个 role factory 都要重写以接受 `prompt_override / extra_context_keys / model` —— 但 F2 的 fix 已经把 reviewer 拉成了"读 directives"模式，沿用同样的 sections-list 方法即可。
- `presets/` 加一层目录 + CLI flag 解析逻辑。CLI 那块 ~50 行新增。
- v0.3 加 tools 时 `NodeSpec` 还要扩；提前在 schema 里给 `tools: list[str] = []` 占个坑可以减少破坏性变更。

**不做的事**：

- v0.2 不做：sub-graphs、自定义状态字段、动态 node 注入、并行 fan-out 后的合并节点。这些都识别为 v0.3+。

## 实现里程碑（对应 [PLAN.md §2.3](../../PLAN.md)）

| # | 任务 | 出口 |
|---|---|---|
| 2.1 | `agent_room/spec.py` schema | pydantic 校验通过；`load_graph_spec` 接受 path/str/dict |
| 2.2 | `presets/full.yaml` + 等价于 v0.1 流水线 | 老 32 个测试 0 改动 |
| 2.3 | `build_from_spec` | preset full 编译出来的图行为等价于 [build_agent_room_graph](../../agent_room/graph.py) |
| 2.4 | role factory 接受 prompt_override / extra_context_keys | 加测试：自定义 prompt 真的换掉了 SYSTEM |
| 2.5 | `presets/dev_review.yaml` + `presets/solo.yaml` | 各 1 个端到端测试，跑通 |
| 2.6 | CLI `--graph` flag | preset 名 + 文件路径都能跑 |
| 2.7 | router dotted-path 解析 | 加测试：自定义 router 能跑 + 解析失败立刻报错 |
| 2.8 | `docs/graph-spec.md` schema 参考 + `examples/solo.py` | README 链得到 |

**v0.3 预留接口**（只占位、不实现）：

- `NodeSpec.tools: list[str] = []` —— v0.3 ADR-0009 的 tools registry 落地；现在把字段加上但 builder 忽略。
- `GraphSpec` 加 `state_class: str | None = None` 字段做 forward-compat 提示，不实现，留给 v0.3 generic state。

## 测试覆盖

参考 [ADR-0002](0002-reviewer-protocol.md) 的测试清单格式：

- `tests/test_spec.py`：
  - `test_full_preset_equivalent_to_legacy_builder`：preset full 跑出来的状态轨迹和 [build_agent_room_graph](../../agent_room/graph.py) 完全一致。
  - `test_invalid_spec_rejected_at_load`：node ref 不存在 / `to` 与 `branches` 同时给 / 缺 entry → ValidationError。
  - `test_solo_preset_runs`：单节点跑通，无 reviewer。
  - `test_dev_review_preset_loops_then_approves`：双角色 revision 循环。
  - `test_router_dotted_path`：自定义 router 加载并被调用。
  - `test_node_prompt_override`：spec 里的 `prompt_override` 真的进了 SystemMessage。
  - `test_extra_context_keys_reaches_human_prompt`：spec 里加 `user_directives` 进 reviewer 的 extra_context_keys，真打到 prompt 上（**直接对应 F2 的修法**）。

## 相关决策

- [ADR-0001](0001-langgraph-as-orchestrator.md)：LangGraph 作为底层。`build_from_spec` 仍然是在 LangGraph 之上薄包装。
- [ADR-0002](0002-reviewer-protocol.md)：reviewer decision enum 是硬合约，spec 只能映射不能扩展。
- [ADR-0007](0007-borrow-not-integrate-hermes.md)：dotted-path router 解析借鉴 hermes-agent tools registry 风格。
- ADR-0009（v0.3，预留）：tools registry + 大输出 spill 存储后端。
