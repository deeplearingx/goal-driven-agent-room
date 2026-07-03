# agent-room — 工程笔记

一个构建在 LangGraph 上的多 agent 协作系统（planner → developer → reviewer →
delivery）。核心约 600 行，离线测试 430+，**零依赖任何外部 agent 框架**。这份
文档不是功能清单，而是**证据**——证明这里的设计选择是对的，每个论点都挂着一个
能复现的数字。

**精确定位（"multi-agent"有两层含义，这里说清）**：它是 Andrew Ng / Anthropic
模式目录里的 **Multi-Agent Collaboration**——多个有独立 prompt 与职责的角色协同。
但它刻意做的是其中**中心化、可控、可评测**的那一种形态:一个带工具的自治 agent
（developer 的 ReAct 循环）+ 三个 LLM 角色,由一个状态机协调,而**不是**去中心化的
自治多 agent 系统(A2A 点对点、动态生成子 agent 树、并行自治)。这个选择是有意的:
中心化才追得清每一步的 trace(标准 OTEL 追踪),也才能逐个开关设计选择做消融(见 §1)。
**"多 agent"不等于"越自治越好";可控 + 可观测 + 可评测的协作,是另一种、也更难证明
其质量的工程取向。**

核心命题：**质量靠测量，不靠声称。** 这套代码里每个能力都是 `NoOp`-默认 + 可插拔
（`NoOpContextEngine`、`NoOpMemoryProvider`、graph preset）。这个属性让系统变成
**它自己的消融实验框架**——逐个开关某个设计选择，看任务成功率怎么动。下面几节就是
这个框架跑出来的结果。

---

## 1. 一个有回报的消融：planner gate

编码 agent 最难的失败模式是**对欠规格的需求贸然动手**——凭空补上六个缺失的决策，
而不是先问。我用同一组四个任务（3 个故意欠规格 + 1 个完整规格作对照）A/B 了两种
graph 拓扑，按**行为**评分：*当且仅当该问的时候停在 `awaiting_user` 才算通过*。

| 变体 | 欠规格（该问） | 对照（不该问） | **通过** |
|---|---|---|---|
| `baseline`（单 reviewer） | 0/3 | 1/1 | **1/4 (25%)** |
| `two_call_review`（reviewer 先 focus-check） | 2/3 | 1/1 | **3/4 (75%)** |
| `planner_gate`（planner 先问） | 3/3 | 1/1 | **4/4 (100%)** |

真实 DeepSeek 跑出。harness 在 [`agent_room/eval/`](../agent_room/eval/)，任务在
[`evals/escalation_tasks.py`](../evals/escalation_tasks.py)，原始输出在
[`snapshots/eval/planner_gate/`](../snapshots/eval/planner_gate/)。

per-cell 细节教给我的东西，我留在记录里而不是抹平：

- **`baseline` 不是简单地"乱猜"。** 三个欠规格任务里有两个把 reviewer 拖过三轮
  修订后*失败*，只有一个 invented-and-shipped。没有升级通道，再称职的 reviewer
  也救不回一个缺失的规格——它只会空转烧轮次。
- **行为通过 ≠ 任务成功。** `planner_gate` 在 `retry_helper` 上正确升级了（我评的
  那个指标），但用户答复回来后下游仍然失败。"问没问"和"成没成"是两个轴；我量了
  这个变体针对的那个，并把缺口讲明。
- **N=1 有噪声。** `rate_limiter` 这个 cell 在两次跑之间翻转过——一次 79s 干净
  在 gate 处升级，一次 254s 修订空转超时。这个方差正是协议定为 **temp=0 + N≥3
  取均值**而非单跑的原因。我**没有**为确定性去建 LLM 响应缓存：带工具调用循环的
  缓存复杂度高、收益虚，是镀金；取均值才是对的工具。

重点不是"100% 很棒"，而是：**一个可替换的设计决策把一个被测量的结果挪动了 4 倍，
而 harness 让这件事可见——连它自己的噪声一起。**

---

## 2. harness 第一跑就值回票价：一个真实的健壮性 bug

*第一次*真实 eval 跑就暴露了一个我靠单元测试永远发现不了的 bug。带工具的 developer
去调 `python -m pytest`（后来又 `cd …`）；shell 白名单用 `ValueError` 拒绝了这个
命令；这个异常直接穿出 ReAct 循环，**中止了整个 run**——状态卡在 `running`，
reviewer 和 delivery 根本没跑。一个健壮的 agent 应该把被拒命令当成一个*结果*去适配。

发现 → 复现 → 修 → 验证，整条闭环：

- **离线确定性复现** —— [`tests/test_react_tool_errors.py`](../tests/test_react_tool_errors.py)
  脚本化一个调被拒 `cd` 的 developer，断言 run 不崩。钉死这个回归不需要真模型。
- **修在正确的层** —— ReAct `ToolNode` 上加 `handle_tool_errors`
  （[`agent_room/graph.py`](../agent_room/graph.py)），把*任意*工具错误转成模型能
  反应的 `ToolMessage`，不止 shell 这一种。
- **隔离人审模式** —— approval 模式的 ToolNode 自己合成 deny `ToolMessage`；在它上面
  叠通用错误处理会破坏两个 approval 测试，所以修复只加在*非 approval* 分支。是那两个
  失败测试抓住了我的过度施工——我收窄，而不是压制。
- **两头验证** —— 离线回归绿，然后真跑当初崩溃的那个任务，干净跑完
  （`running` → `completed`）。

这正是 eval harness 存在的意义所在的那条闭环：一类只有真模型驱动真工具时才出现的
失败，被一个确定性测试抓住并关掉。

### 2.1 真实负载证伪了一个"看起来对"的组件

第二个例子更微妙。上下文压缩引擎有完整的离线测试——证明它*机械上*正确：消息序列
合法、压缩幂等。但离线测试用合成历史，从不显示压缩**如何影响真 agent 的行为**。在一
个多文件 ReAct 调试任务上做四向消融（同任务，只换压缩策略）：

| 引擎 | 触发 | 结果 | ReAct 消息 | tokens |
|---|---|---|---|---|
| 不压缩 | — | ✅ 通过 | 15 | 12K |
| Windowed（丢中段） | message-count | ❌ **失败** | 37（churn） | 17K |
| Summary（摘要中段） | message-count | ✅ 通过 | 34 | **40K** |
| Summary（摘要中段） | **token 压力** | ✅ 通过 | **22** | **21K** |

三层结论，每层都是真实负载逼出来的：(1) **丢中段摧毁工作记忆**——developer 原地打转
重新探索，烧更多 token 还失败（复现三次）。(2) **摘要而非丢弃**保住记忆得以收敛（借自
参考架构）。(3) 但摘要用 message-count 触发会**过度压缩**（最贵：40K token）——改成
**按 token 压力触发**（引入 tiktoken）后既正确又省一半（21K / 快 2.2x）。一个有完整
单元测试、看起来没问题的组件，在真实负载下先是有害、再是低效——只有放进真 agent 跑、
再据数据逐层修，才看得见、也才修得对。

### 2.2 这不是个例：把每个子系统放进真 agent 跑，每个都掉出一个真 bug

上面两个不是运气。**eval harness 每接入一个子系统，第一次真跑就暴露一个离线测试
看不见的缺陷**——这才是它真正的价值：

| 子系统 | 真实负载暴露的缺陷 | 修复 |
|---|---|---|
| 工具策略 | 被拒命令(`cd`)抛异常**炸掉整个 run** | 错误回灌 LLM 而非崩溃（F1） |
| 结构化输出 | 思考模型 `tool_choice` 被拒 + 返回 None **崩 `.model_dump()`** | transport JSON 回退，保证非 None（F2/F3） |
| 上下文压缩 | 丢中段 **churn 到失败**；摘要又过度触发 | 摘要 + token 压力触发（见 §2.1） |
| 跨会话记忆 | provider **从没被初始化**，配了也静默失效 | graph builder 拥有 provider 生命周期 |

四个子系统、四个真 bug，没有一个能被离线单元测试抓到——它们只在真模型驱动真工具、
真负载下才现形。**eval-driven development 不是"测我写对了没"，是"让系统在真实里暴露
它哪里其实是错的"。**

---

## 3. 三个反直觉的设计判断

判断力体现在那些**不是默认路径**的决定上。

**冻结 memory 快照以保护 prefix cache。** 跨会话记忆
（[`agent_room/memory/`](../agent_room/memory/)，ADR-0010）把 curated `MEMORY.md`
渲染进 system prompt **每会话一次**然后冻结，即使模型在会话中途写入新事实也不重渲。
朴素做法是每次写入都重渲——而那会**每一轮都让 provider 的 prefix cache 失效**。在
一个 2K-token 的 system prompt 上跑 30 轮 ReAct，就是约 60K 个被重新计费的 cached
token。冻结把它塌成 2K；新事实下个会话生效。会话内召回由独立的 FTS transcript 层
覆盖。

**压 prompt，不压 state。** context engine 限制的是*发给 LLM 的*消息列表；它从不动
`state["dev_messages"]`——后者在 LangGraph reducer 下保持 append-only（审计和重放
需要全量历史）。把"模型看到什么"和"我们持久化什么"混为一谈是容易犯的错；把它们分开，
才让崩溃恢复和上下文压缩能共存。

**graph 按 (variant, task) 建，不按 variant 建。** eval runner
（[`agent_room/eval/runner.py`](../agent_room/eval/runner.py)）为每个 cell 编译一个
新 graph。"每 variant 一个 graph"看着像那个优化——直到你注意到 graph 编译是微秒级，
而 LLM 调用是秒级，而且按 variant 缓存会挡住按 task 的沙盒工具注入。看穿一个过早
优化，免费消掉了一整类耦合。

---

## 4. 用"不做什么"体现判断

我选择*不*做的东西，也是设计的一部分：

- **不依赖外部框架。** "借鉴模式，不 import 代码。" 用运行时硬测强制，而非 grep：
  [`tests/_independence_driver.py`](../tests/_independence_driver.py) 在一个拦截上游
  包的 meta-path hook 下强制导入每个子模块——60 个子模块，全绿。
- **不给生产基础设施镀金。** Postgres / Prometheus / OpenTelemetry / PyPI 被明确
  移出关键路径。"我会配基础设施"相比"我能测量并论证 agent 质量"是弱信号。见
  [`PLAN.md`](../PLAN.md) 北极星一节。
- **不建 LLM 响应缓存。** 确定性靠 temp=0 + N≥3，见 §1。

---

## 5. 站在 2026 标准上：OpenTelemetry + MCP

2026 的 agent 领域有个共识转向：能力是 **harness（基础设施）的属性**，不是模型的属性；
栈在标准化成 model→runtime→harness→agent。我据此补了两块**对得上行业标准、又不破坏
极简/独立调性**的能力。

**OpenTelemetry 追踪**（调研里被反复点名为全行业第一大可观测性缺口）。一次 task 出一棵
标准 span 树——`agent_room.task` → 每个 `node.<role>` → 每次 `llm.invoke` / `llm.structured`，
LLM span 用 **GenAI 语义约定**（`gen_ai.operation` / `gen_ai.usage.*`），直接落进任何标准
后端（Jaeger / Tempo / Honeycomb）。关键判断：**零影响默认**——没配 provider 时是 no-op，
不强制任何外部服务（独立性不破）；transport 是天然埋点位（token 本就在
`NormalizedResponse.usage`）。`structured_fallback` 属性让你在 trace 里直接看到哪些调用走了
§2 的 JSON 回退。

**MCP 客户端**（2026 工具集成的事实标准）。一个薄 adapter 把任意 MCP server 的工具加载成
LangChain `BaseTool` 注册进自研 Registry——任何角色像用内置工具一样用，一行接入整个 MCP
生态。借 `langchain-mcp-adapters` 做协议、自己只做 registry 桥（ADR-0007），**可选依赖**
（`pip install 'agent-room[mcp]'`，核心包不变重）。离线测试用一个 in-process FastMCP server
起 stdio 子进程做**真协议往返**，不碰网络。

共同点：**用行业标准（OTEL semconv / MCP），不自创格式；薄桥接，不重造协议；默认零开销，
不破独立性。** 站上 2026 前沿，同时守住"用最少代码"。

---

## 6. 诚实的局限

- **单 provider、小 N。** 数字只来自 DeepSeek，每 cell N=1。协议是 temp=0 + N≥3 取
  均值，但 N≥3 还没跑（纯成本，不是架构）——所以单条消融结果带 N=1 噪声，我在正文里
  逐条标了（如 §1 的 rate_limiter 跨 run 翻转）。
- **区分度是 per-axis 的。** 强模型把简单正确性任务全过（coding 套件默认 provider
  零覆盖 **13/13**）；只有针对某个轴的任务（gate 的欠规格 / 多文件长上下文 / 跨会话
  recall）才区分得出变体。一个套件证明"跨场景能跑通"，另一套证明"某个设计有用"。
- **上下文窗口是近似查表。** `context_window.py` 的模型→窗口映射是手维护的近似值
  （未知模型回退 128K）；token 预算够用，但不是从 provider API 拿的精确值。

---

*复现：`pip install -e '.[dev]'` 然后 `pytest -q`（离线，无需 key）。真实 eval 需要
provider——见 [`evals/run.py`](../evals/run.py)。*
