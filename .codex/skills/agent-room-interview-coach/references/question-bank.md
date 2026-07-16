# Project-Specific Question Bank

Select questions adaptively. Ask one at a time and use follow-ups only after the candidate answers.

## Gate 1 — Positioning

1. 用 30 秒介绍这个项目：它解决什么问题，你最关键的工程贡献是什么？
2. 这个项目和“套壳调用多个模型”有什么本质区别？
3. 如果简历只能保留两条 bullet，你会保留哪两条，为什么？

Signals: crisp problem, architecture, personal ownership, one measured result, honest scope.

## Gate 2 — Architecture

1. 在白板上画出 workflow mode 和 goal mode，它们的终止条件分别是什么？
2. 为什么选择 LangGraph，而不是自己写 while-loop，或直接使用 CrewAI/AutoGen？
3. 哪些决策应该由确定性代码做，哪些应该交给 LLM？
4. 状态、checkpoint、resume 和 SSE 之间是什么关系？
5. 为什么 `dev_messages` 需要 append-only？代价是什么？

Follow-ups: crash recovery, idempotency, state drift, structured output, graph preset persistence.

## Gate 3 — Hard-task failure

1. 目标模式遇到难任务为什么会“失败”？请从现象讲到根因，不要只讲最后改了什么。
2. `max_iterations=50` 为什么仍可能在第 50 轮之前触发 LangGraph recursion error？
3. 你如何证明修复不是“把 recursion_limit 调大一点”？
4. Windows 路径解析为什么会与这个故障纠缠在一起？
5. 为什么恢复任务时保留请求所选 graph preset 是正确性要求？

Follow-ups: worst-case formula, supervisor cadence, replan overhead, regression boundary, framework vs product budgets.

## Gate 4 — Verification, safety, evaluation

1. reviewer 已经能审查，为什么还需要 objective verifier？
2. developer 如果修改测试文件让自己通过，系统如何防止？
3. shell 工具的信任边界是什么？allowlist 能防住什么、不能防住什么？
4. 616 个测试和一次 DeepSeek 实测分别证明了什么，不能证明什么？
5. 你怎样为 agent 系统设计 eval，而不是只测单个 Python 函数？

Follow-ups: oracle quality, nondeterminism, N>=3, false positives, prompt injection, auth boundary.

## Gate 5 — Tradeoffs and system design

1. 如果要支持 1 万个并发任务，你会先替换哪些组件？
2. 如何把 detached SSE run 扩展成多实例服务，同时保持重连和事件顺序？
3. 当前 per-tenant SQLite 隔离方案有什么优点和上限？
4. 如何设计可观测性来定位“模型弱、工具失败、路由错误、上下文丢失”中的哪一种？
5. 如果验证器本身昂贵或不稳定，你会怎样设计分层验证？
6. 为什么上下文不能简单保留最近 N 条消息？

Follow-ups: queue, leases, idempotency keys, event log, Postgres, object storage, tracing, SLOs, cost controls.

## Gate 6 — Coding and debugging

1. 给定 `max_iterations` 和每三次失败一次 supervisor，写出安全 recursion budget 的计算方法和边界测试。
2. 设计一个测试，证明 reviewer feedback 确实进入下一次 ReAct developer prompt。
3. 修复一个跨平台命令解析函数，同时保持 allowlist 语义。
4. 设计一个 property-based test，验证路径解析永远不能逃出 workspace。
5. SSE 客户端断线重连后出现重复事件，你会在哪里去重，为什么？

Require narrated hypotheses, smallest reproduction, invariant, regression test, and operational consequence.

## Gate 7 — Behavioral

1. 讲一次你原先的设计被真实负载证伪的经历。
2. 讲一次测试抓住你“修过头”的经历。
3. 讲一次你主动缩小 scope 的决定，以及为什么这不是偷懒。
4. 讲一次你如何处理不确定、不可复现或小样本的模型行为。
5. 讲一次你发现问题不在模型，而在基础设施的经历。

Use STAR or CARL, but preserve the repository's real scale and solo/team context.

## Full mock mix

For a 45-minute mock:

- 5 min positioning and resume drill
- 10 min architecture deep dive
- 10 min hard-task failure investigation
- 12 min system-design extension
- 5 min behavioral question
- 3 min candidate questions

Hold feedback until the end in strict mock mode.

