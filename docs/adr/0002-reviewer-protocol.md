# ADR-0002：Reviewer 用 `with_structured_output` 而不是 JSON 解析

- 状态：accepted
- 日期：2026-06-11
- 关联：[CLAUDE.md §3](../../CLAUDE.md)、[ADR-0001](0001-langgraph-as-orchestrator.md)、[architecture.md §4](../architecture.md)

## 上下文

`reviewer` 节点的输出是整张图的**唯一路由信号**——三种 decision 决定下一步走哪条边：

```
approved              → delivery
revision_required     → developer (revision_round++ 直到耗尽 max_revisions)
need_user_decision    → END (halt，等 service.resume 续跑)
```

[graph._route_after_review](../../agent_room/graph.py#L21-L31) 只看 `state.review.decision` 的字面值。
任何"非这三个值"的输出都会让条件边静默走默认分支或抛 `KeyError`，整张图就废了。

历史方案是 TS 版的 `parseReviewerJson`：让 LLM 返回 JSON、用正则 + 字段补齐做容错。失败模式有三类：
- LLM 漏字段（少 `decision` 或 `confidence`）
- LLM 把字段值随便写（`"decision": "looks ok"`，不是三个 enum 之一）
- LLM 用 markdown 包了一层 ```` ```json ```` 围栏，要剥皮

每次新模型上线都要补一条 hack。

## 决定

Reviewer 节点**必须**用 LangChain 的 `BaseChatModel.with_structured_output(ReviewerDecision)`，
让 provider 自己保证输出是合法 `ReviewerDecision`。
**禁止**回退到字符串解析、正则、或"出错就当 revision_required"这种容错策略。

具体落到代码：

```python
# agent_room/roles/reviewer.py
structured = llm.with_structured_output(ReviewerDecision)
decision: ReviewerDecision = await structured.ainvoke(msgs)
```

`ReviewerDecision` 的 schema 定义在 [agent_room/schemas.py](../../agent_room/schemas.py)：

```python
ReviewDecisionLiteral = Literal["approved", "revision_required", "need_user_decision"]

class ReviewerDecision(BaseModel):
    decision: ReviewDecisionLiteral          # 路由信号
    feedback: str                            # 给 developer / 用户看的人话
    issues: list[str] = Field(default_factory=list)
    confidence: float = Field(default=0.8, ge=0.0, le=1.0)
```

## 三值合同

| decision | 语义 | 路由 | 谁来"消费" feedback | 何时用 |
|----------|------|------|---------------------|--------|
| `approved` | 代码符合 plan，可以交付 | → delivery | delivery 节点拼进 handoff 文档（"Reviewer notes" 段） | 没 issues 或 issues 都已经修了 |
| `revision_required` | 有可修的具体问题 | → developer（`revision_round` +1） | developer 节点拼进下一轮 prompt（"Reviewer feedback" 段 + "Issues" 列表） | 必须把所有阻塞性 issue 都列在 `issues` 字段里，以便 developer 逐条解决 |
| `need_user_decision` | 团队权限之外的歧义（产品/法律/架构选择） | → END (halt) | service.resume 时把用户答复追加到 [`state.user_directives`](../../agent_room/state.py#L71)，下一轮 developer 读 | 仅当问题是**本质性歧义**——不是"我不会"，而是"这件事 LLM 不该自己拍板" |

**`feedback` 字段的规则**：

- `approved` / `revision_required`：写给 developer 或 delivery 看，必须可执行（说清楚问题在哪、怎么改）。
- `need_user_decision`：写给**用户**看，必须是个具体问题（"用 lib A 还是 lib B？"），不是「不确定」这种表述。这个字段会出现在 SSE 流和 CLI 输出里。

**`confidence` 字段的规则**：

- 不参与路由判断，只供观测/UI 显示。
- 现阶段不做基于 confidence 的决策（不要写 `if confidence < 0.5: route_to_user`）。如果未来要引入，得另写 ADR。

## 不变量（图层面，必须由代码保证）

1. 整个 `reviewer` 节点函数里，`decision: ReviewerDecision` 这一行不会因为 LLM 返回畸形数据抛异常。
   `with_structured_output` 在 OpenAI / Anthropic / Google 上都已经做了 schema 校验。如果某 provider 没做，这条不变量被打破，**应该报错给用户而不是降级**。
2. `_route_after_review` 只对 `decision` 字面量分支，不读 `feedback` 或 `confidence`。
3. `revision_round` 只在 reviewer 节点 +1，其他节点不能修改它。
4. resume 路径写 reviewer state 时（[service.resume](../../agent_room/service.py#L114-L149)），decision 必须是合法字面量之一。当前用 `revision_required` + `feedback="See user directive in state.user_directives"`。

## 备选与拒绝原因

**JSON 字符串解析（TS 旧方案）**：
依赖 LLM 自觉，每次新模型行为飘忽都得补容错。本质是把契约保证推给 prompt engineering，
不可持续。`with_structured_output` 把契约下沉到 provider 层，LangChain 已经吸收了
function-calling / JSON mode 的 provider 差异。

**自定义 OutputParser + Pydantic**：
LangChain 的 `PydanticOutputParser` 也能做，但它是基于"在 prompt 里贴 schema 让模型模仿"的弱保证，
provider 不参与校验。`with_structured_output` 在支持的 provider 上会用 native function calling /
JSON mode（OpenAI），强约束远胜过 prompt-only 方案。

**两个独立 LLM 调用（先 free-form 思考，再 forced-format）**：
更准但贵一倍且慢一倍。本项目的 reviewer 不需要 chain-of-thought，直接 forced-format 已经够用。

## 后果

**好处**：
- reviewer 节点 < 30 行，没有任何容错代码。失败立刻报错而不是降级。
- 路由逻辑可以静态推理（`_route_after_review` 是纯字典分发）。
- pydantic schema 是文档：调用方一眼看到合同。

**代价**：
- 锁定 "provider 支持 structured output" 这条假设。某些本地模型（Llama 系 + 直连推理服务器）目前做不到强校验，要么不支持 reviewer 角色，要么前面套 `with_structured_output(method="json_schema")` 兜底（[RISK-2](../../PLAN.md)）。
- `decision` literal 的取值集合是图的硬合约，扩值（比如加个 `escalate_to_pm`）必须同时改 schema、`_route_after_review`、ADR。

## 测试覆盖

[tests/fakes.py](../../tests/fakes.py) 的 `FakeReviewerLLM.with_structured_output` 返回一个 `FakeStructured`
对象，按预设序列吐 `ReviewerDecision` 实例。所以单元/集成测试不依赖真实 provider，
但仍然走"reviewer 输出是 `ReviewerDecision` 实例"这条不变量的代码路径。

测试里覆盖了三个 decision 的全部分支：
- [test_graph.py::test_happy_path_approves_first_try](../../tests/test_graph.py)（approved）
- [test_graph.py::test_revision_loop_then_approval](../../tests/test_graph.py)（revision_required → approved）
- [test_graph.py::test_halts_when_revisions_exhausted](../../tests/test_graph.py)（revision 用尽 → failed）
- [test_graph.py::test_need_user_decision_halts_then_resume_completes](../../tests/test_graph.py)（need_user_decision → resume）
- [test_user_directives.py](../../tests/test_user_directives.py)（resume 走 user_directives 不偷渡 feedback）

## 相关决策

- [ADR-0001](0001-langgraph-as-orchestrator.md)：选 LangGraph，结构化输出是其原生能力之一
- [ADR-0008](0008-graph-spec.md)：v0.2 GraphSpec YAML 配置 —— `NodeSpec.variant` 让同一个 role 可以挂不同的结构化合同（见下方"后续修订"）

## 后续修订

### 2026-06-11 — Reviewer prompt heuristics（F2 escalation lab 收尾）

[Smoke 发现 §F2](../findings/2026-06-11-smoke-v0.1.md#f2-reviewer-never-returns-need_user_decision-)
显示 reviewer 在 under-specified 任务上几乎永远不返回 `need_user_decision`：训练分布
让模型把任何编造的代码当成 `revision_required` 去找茬，而不是 escalate。验证过
"prompt 改写"路径走不通后，v0.2 §2.9 把三个备选方案做成可选**变体**，不动 ADR-0002 的核心合同：

| 选项 | 实现 | 选用方式 |
|------|------|----------|
| Planner-side gate | `roles/planner_gate.py`（结构化 `PlannerGateOutput = {plan, open_questions, rationale}`） | spec：`nodes.planner.variant: gate` + `presets/planner_gate.yaml` |
| Two-call reviewer | `roles/reviewer_two_call.py`（先调 `FocusCheck` 再调 `ReviewerDecision`） | spec：`nodes.reviewer.variant: two_call` + `presets/two_call_review.yaml` |
| 模型替换 | `NodeSpec.model` 字段（§2.4 已有） | spec：`nodes.reviewer.model: <id>` |

- `ReviewerDecision` 这个核心合同**没动**。三个变体仍然以 `ReviewerDecision`
  作为路由信号；planner-gate 和 two-call 在 escalate 时合成一份
  `ReviewerDecision(decision="need_user_decision", ...)` 写到 state，所以
  下游的 `review_router` / `_materialize_status` / `service.resume` 都不知道
  "升级路径"是上游谁决定的。这是 ADR-0002 "结构化输出 = 路由的唯一信号源"
  的延续。
- `service.resume` 加了 `at_node="planner"` 选项专门给 planner-gate 用：
  gate 在 planner 节点 halt，需要从 planner 重进，不是从 reviewer 重进。
- 无 provider 时 lab 优雅跳过，离线测试覆盖在 `tests/test_escalation_variants.py`
  + `tests/test_escalation_lab_runner.py`（10 个测试，63/63 全绿）。

**真实 LLM 实测（2026-06-12，DeepSeek-V4-Pro via Ark，3 个 under-spec 场景，per-run 600s timeout）**：
- `baseline`：0/3 (0%) — F2 完全复现
- `planner_gate`：2/3 (67%) — 远超 ≥30% 目标
- `two_call_review`：1/3 (33%) raw / 1/1 (100%) 排除 provider 偶发 `OutputParserException` 后

详见 [findings F2 §"v0.2 §2.9 — escalation lab measurement"](../findings/2026-06-11-smoke-v0.1.md)。
推荐：需要 escalation 时优先用 `planner_gate` —— 它在 ambiguity 还没污染代码前就 halt，
`open_questions` 列表本身就是给用户看的工件，且只调一次结构化 LLM（`two_call_review` 调两次，
撞 provider parser-error 的概率翻倍）。
