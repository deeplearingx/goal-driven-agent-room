# PLAN.md — agent-room 详细路线图

> 配套：[CLAUDE.md](CLAUDE.md)（项目宪法）。本文件可以变，宪法不能轻易变。
> 任何超过 3 个文件的改动、引入新依赖、修改公开 API 都先在这里立项。

> **重要原则**：本项目独立运行，**不依赖** hermes-agent。
> 路线图中所有"借鉴 hermes 模式"的条目，都是**重新实现**到本仓库内，
> 而不是 import / 桥接。详见 [CLAUDE.md §1.1](CLAUDE.md)。

---

## 项目目标（北极星）—— 2026-06-23 重定向

**定位**：本项目是**作品集证明**，不是产品、不是框架。它要回答的唯一问题是——
*"作者能不能在多 agent 系统上做出 senior 级的工程判断，并用数据证明这些判断是对的？"*

**北极星指标**：一个**消融实验表**（ablation table）。在一个固定的真实任务集上，
逐个开关本项目的设计选择（planner_gate / two-call review / context engine /
memory / transport），用**任务成功率 + 成本/延迟**量化每个设计的贡献。

> 关键洞察：本项目所有能力都是 **NoOp-default + 可插拔**（`NoOpContextEngine` /
> `NoOpMemoryProvider` / preset 变体）。**这套架构本身就是一个现成的消融框架**——
> 之前没人把这点连起来。v1.0 的工作就是把它兑现成数字。

**三条出口标准（达成即"作品可投"）**：
1. **EVAL**：`make eval` 在 ≥ 12 个真实任务上跑出成功率表 + ≥ 4 组消融对照，
   数字可复现（固定 seed / 缓存的 LLM 响应）。
2. **WRITEUP**：一篇 [`docs/portfolio.md`](docs/portfolio.md) 技术叙事，讲清 3-4 个
   反直觉设计判断（reviewer-as-contract / frozen-snapshot 保 prefix-cache /
   压 prompt 不压 state），**每个判断挂一个 eval 数字**。
3. **可读性**：PLAN §0 流水账压成一页 changelog；任何人 5 分钟看懂项目做了什么、
   为什么这么做。

**明确砍掉**（不服务北极星，移到"以后再说"）：Postgres checkpointer、Prometheus
metrics、PyPI 打包。这些是"我会配基础设施"的弱信号；相比"我能测量并论证 agent
质量"，投入产出比太低。
> **2026-06-23 修订**：OpenTelemetry **捡回并已落地**——调研显示它是 2026 全行业第
> 一大可观测性缺口，且跟"harness engineering"主线一致，不是弱信号（详见 §0 OTEL 条目）。
> structlog 同理留作 v1.0 收口（B1）。

---

## 0. 当前状态 — changelog

> 详细落地记录（每条含测试数 / 独立性 / 设计权衡 / 故意没做的取舍）已归档到
> [docs/archive/ledger-v0.x.md](docs/archive/ledger-v0.x.md)。本节一行一里程碑。

**当前**：v1.0 重定向为**作品集证明**（见上"北极星"）。**615 离线测试全绿**，
mypy strict 0 错，独立性 76 子模块，coverage ≥ 90%。EVAL harness + 12 任务 + 首张
真实消融表 + 作品集叙事均已落地；**12 任务 coding 套件默认 provider 零覆盖跑通**，
F1/F2/F3 三个真实 bug 已修，结构化输出在思考模型上开箱即用，纯管道 token 采集补齐。
§6.9 gap-closing roadmap 四项（MCP/预算/guardrail/混合语义记忆）**全部完成**；
§6.15 加了 Qdrant 可插拔向量后端 + 全持久化层物理级租户隔离；§6.16 落地
**双模式**（workflow + goal——给定目标持续迭代直到客观验证通过）。

| 日期 | 里程碑 |
|---|---|
| 2026-07-02 | **§6.16 目标模式（goal mode）——项目第二运行模式**：`AGENT_ROOM_GRAPH=goal` 起 develop→verify 循环,退出条件从"修订预算耗尽"倒转为"客观 oracle 通过"（`TaskRequest.verify_command` exit 0;没配则退 reviewer 主观 approved;`max_iterations` 只是防失控兜底,触发即 failed)。混合路由:机械决策全确定性(`goal_router`),连续失败 3 轮(`STUCK_EVERY`)确定性绕道一次 `supervisor` 做 `StuckDecision` 四选一(continue/replan/ask_user——复用 need_user_decision 契约零改动接现有暂停恢复/abort)。**顺带修主线真 bug**:ReAct developer 修订轮拿旧 transcript 盲目重试、reviewer 反馈永远看不见——确立 transcript-push 统一模式(reviewer/verifier/planner 在 `dev_messages` 非空时把新信息 append 进去+重置 `dev_round`,空时零行为变化)。verifier 非 LLM 纯框架节点:verify_command 过同一 shell 白名单、cwd pin 任务 workspace、60s 超时;`verify_files` 每轮 reseed **机制性堵死"developer 改测试作弊"**(e2e 专门用例:篡改 check.py 被 reseed 击败)。顺手对齐 `_materialize_status` 与 `review_router` 的 max_revisions 缺省值 0/2 预存在不一致。**41 新测试**(verifier 12+路由 13+transcript-push 7+真编译图 e2e 9),**615 全绿、mypy strict 0 错(76 文件)、独立性 76、ruff clean**。**真 LLM 冒烟**(DeepSeek-V4-Pro):median 任务描述说"standard behavior"但 check 偏要下中位数,真实轨迹 verify(r1 FAIL)→developer→verify(r2 PASS)→delivery,最终代码 docstring 采纳了**只在 r1 失败输出里出现过**的规则——坐实失败反馈真进了下一轮 prompt(详见 §6.16) |
| 2026-07-01 | **§6.15 Qdrant 可插拔向量后端 + 物理级租户隔离**：详见下方 §6.15 独立章节。核心结论：给每个租户一个独立 `db_path` 这一件事同时解决了 checkpointer/curated/transcript/sqlite-vec 四层隔离,比"共享表+`tenant_id`过滤列"更彻底也更简单；新增 `VectorIndex` Protocol + `QdrantVectorIndex` + `FastEmbedBackend`（默认模型 `jinaai/jina-embeddings-v2-base-zh`,中英双语,经 `fastembed.TextEmbedding.list_supported_models()` 验证非猜测）；三档消融——语序打乱复述 FTS5-only 0/4、sqlite-vec+hashing 4/4、Qdrant+fastembed 4/4（打平）；**零字符重叠真同义词**（"喵星人"/"猫"）FTS5-only 0/4、hashing **3/4**（部分靠巧合字符重叠命中）、Qdrant+fastembed **4/4**——这才是"真语义 embedding 该赢的地方"，用真实数字证明而非空口叙事。**20 新测试,574 全绿、独立性 74（70→74，新增 vector_index.py + qdrant_index.py + fastembed_backend.py + server/tenancy.py）、mypy strict 0 错**（含一次真实 mypy+numpy2.x 兼容性问题的诊断与修复，见下）。 |
| 2026-07-01 | **§6.9-4 混合语义/向量记忆（收官 §6.9 全部四项）**：`TranscriptStore`（`fts.py`）加可选 `embedding:` 构造参数，新增 `agent_room/memory/embedding.py`（`EmbeddingBackend` Protocol、`NoOpEmbeddingBackend` 默认、`HashingEmbeddingBackend`——依赖零增量的字符 n-gram 特征哈希，stdlib `hashlib`+`math`，不是深度/transformer embedding，诚实记在 SECURITY.md）+ `agent_room/memory/vector.py`（`VectorStore` 包 sqlite-vec，`vec0` 表按 `rowid` 对齐 transcript 消息 id，`aiosqlite.Connection` 没有公开的扩展加载 API，落到私有 `_execute`/`_conn` 是 aiosqlite 自己推荐的扩展加载写法）。`search()` 融合两路——lexical（既有 FTS5+LIKE）与 vector KNN——用 reciprocal rank fusion（标准常数 60）按 message id 去重排序，未配置 embedding 时零改动（`embedding=None` 默认，既有全部 FTS5 测试不改一行仍绿）。`AGENT_ROOM_MEMORY_VECTOR`(默认 0) 开关，`Settings.embedding_backend()` 关时返回 `None` 而非 truthy 的 `NoOpEmbeddingBackend()` 实例——**开发中发现并修复一次真 bug**：最初实现让 `build_server_memory` 无条件传一个 embedding 实例（NoOp 或 Hashing）给 `FileFtsMemoryProvider`，导致哪怕关着开关，`TranscriptStore` 也会因为 `embedding is not None` 而尝试用 `dim=0` 建 `vec0` 表，违反"NoOp-default 零行为变化"铁律；测试 `test_build_server_memory_no_vector_by_default` 断言 `_embedding is None` 时抓到，改为 `embedding_backend() -> EmbeddingBackend | None` 关时真返回 `None` 修复。`/healthz.tool_envelope` 报 `memory_vector`/`memory_vector_backend`。**消融数字（PLAN 北极星要求的那类）**：4 组重排序 CJK 复述 query/fact 对（FTS5 MATCH 因词序打乱且无公共子串失效、LIKE 整串子串匹配同样失效）——**FTS5-only 命中率 0/4，hybrid 命中率 4/4**，字符 trigram 哈希在词序被打乱时仍能靠共享 n-gram 找回，真实证明"补齐无向量召回硬差距"这句话不是空话。**真实服务器联调**：`AGENT_ROOM_MEMORY_VECTOR=1` 起服务器，`/healthz` 正确报 `memory_vector:true, memory_vector_backend:"hashing"`；提交一个真实 DeepSeek-V4-Pro 任务后直接查 `agent_room.db`——`agent_room_memory_vectors`（sqlite-vec `vec0` 影子表）与 `agent_room_memory_messages` 行数**完全一致（7/7）**，证明真实开发者轮次被正确嵌入索引，不只是离线测试通过。**21 新测试**（embedding/VectorStore/RRF/hybrid wiring 16 + config/envelope/wiring 5），**554 全绿、独立性 70（68→70，新增 embedding.py + vector.py）、mypy strict 0 错**（新增一条 `agent_room.memory.vector` 的 `no-untyped-call` 豁免，边界原因同上——aiosqlite 私有方法未标类型，非产品代码 bug） |
| 2026-06-30 | **§6.9-3 通用 Guardrail 层（4 检查点）**：新模块 `agent_room/guardrail.py`（`Guardrail`/`GuardrailTripwire`/`scan()`），把 `memory/curated.py` 的注入检测正则收编成唯一权威源。4 个**可达**检查点：**input**（`api.py` 图跑之前，block 模式直接 400）、**tool_call**（`write_text`/`shell` 新增可选 `guardrail:` 字段，故意只做这两个内置工具不做全工具 `awrap_tool_call` wrapper——会跟 `tool_mode=approval` 占同一个 LangGraph 扩展点）、**tool_response**（`developer_react.py` 重入时扫最新一批 `ToolMessage`——本次最高杠杆新增点，防恶意/被攻陷 MCP server 通过工具返回值做 prompt injection，且天然覆盖内置+MCP 全部工具）、**output**（`delivery.py` 最终交付文本）。`block` 命中复用 §6.12 的 `RunManager` 异常兜底转 `task_error(guardrail_blocked=true)`，`warn` 命中落 `TaskState.events`。`AGENT_ROOM_GUARDRAIL`(off/warn/block) env 可配，`/healthz.guardrail` 报告 mode，NoOp-default 零行为变化。**真实服务器联调**：`AGENT_ROOM_GUARDRAIL=block` 起服务器，带越狱短语的 `POST /tasks`/`POST /tasks/stream` 两次真实复现 `400 {"detail":"rejected by guardrail..."}`，图从未启动；干净请求正常放行。一次真实 LLM 意外行为诚实记录在案（DeepSeek 面对不存在的文件时创造性地自己写了段"演示用恶意代码"而非报错，guardrail 正确未拦——因为这不是 prompt injection 模式，边界符合设计）。**25 新测试**（核心模块 10 + tools 6 + developer_react 2 + graph 2 + api 5），**533 全绿、独立性 68、mypy strict 0 错** |
| 2026-06-30 | **§6.9-2 Token/成本预算 + 熔断**：`agent_room/budget.py` 新模块（不进 `agent_room/llm/`,避让 ADR-0013 文件/行数封顶）；`graph.py` 每次建图共享一个 `LangChainTransport(budget=BudgetTracker(...))`,通过既有 `transport=` 注入点传给全部角色节点,同任务用量累计在一处;超限从 `invoke()` 抛 `BudgetExceededError`,节点不捕获自然外传,被 `RunManager` 通用异常兜底转成 `task_error(budget_exceeded=true)`(复用 §6.11,零新服务层代码)。`AGENT_ROOM_MAX_TOKENS`/`MAX_TOOL_CALLS`/`MAX_COST_USD`/`PRICE_PER_1K_*` env 可配,`/healthz.budget` 报告天花板,NoOp-default 零行为变化。**两处诚实 scope cut**(只做 env 级非 per-request;累加器不进 checkpointer 故 resume 后清零)+ **中途发现并删除一处死代码**(`structured()` 的"preflight 拦截已超限"检查,逻辑推演后发现该状态在当前架构下不可达,直接删而非留着凑数)——都写进 SECURITY.md + PLAN §6.12 + 模块 docstring。**真 LLM 实测**:`AGENT_ROOM_MAX_TOKENS=50` 起服务器,真任务 planner 首次调用耗 775 token 立刻越界,SSE 收到 `task_error{budget_exceeded:true, budget_dimension:"max_tokens", budget_limit:50, budget_actual:775}`。**19 新测试,508 全绿、独立性 67、mypy strict 0 错** |
| 2026-06-30 | **§6.9-1 MCP 工具接上线**：[`tools/mcp.py`](agent_room/tools/mcp.py) 的适配器早已写好但没接进 server——`AGENT_ROOM_MCP_SERVERS`（JSON: server 名→langchain-mcp-adapters 连接配置）在 lifespan 里**只连一次**（`load_server_mcp_tools`），发现的工具实例在每个 per-task registry 间**共享复用**（MCP 工具本就无状态、每次调用自己开短连接，天然安全共享）；动态并入 developer 的 spec.tools + `/healthz.tool_envelope` 报告 `mcp_servers`/`mcp_tools`。**安全**：MCP 工具无 `read_only` 元数据，`is_read_only` 保守判非只读，天然继承既有 `tool_mode` 权限过滤（read_only 模式自动排除、approval 模式逐次过审）；信任边界是运维配置的 server 列表而非 LLM；JSON 畸形/连接失败**启动即崩**（不静默降级）。**真 MCP server 实测**（复用仓库自带 `tests/_mcp_test_server.py` stdio add 工具，零网络依赖）：`/healthz` 正确报 `mcp_servers:["calc"]`/`mcp_tools:["add"]`；真 LLM 任务里 developer 真调 `add(48173,96215)` 拿到工具算出的 `144388`（非心算，算术校验一致）、写文件、per-task workspace 隔离保持；畸形 JSON 配置实测启动即抛 `JSONDecodeError` 崩溃（非静默）。**11 新测试**（配置解析 4 + spec/registry/envelope 集成 7），**489 全绿、独立性 66、mypy strict 0 错** |
| 2026-06-29 | **SSE 稳健性三件套（detached run 架构）**：(1) **断线重连+续传**（§6.11）——`RunManager` 把运行脱离连接跑在后台 + seq 环形缓冲，`GET /tasks/{id}/events?from=N` 重连续传；真 LLM 掐断 6s 后台跑完、`from=1` 续到 completed。(2) **心跳保活**——两流端点显式 `ping=AGENT_ROOM_SSE_PING`(默认 15s)，静默期照发 `: ping` 防代理超时。(3) **停止任务**——`POST /tasks/{id}/cancel` 取消后台 run + 发终态 `task_error(cancelled)`；前端 header「停止」按钮 → abort 本地流 + cancel + 标已中断；真 LLM 取消大任务停在 planner。同期修 `get_task` 对已知未完成 session 误判 404、per-task workspace 隔离、生成文件浏览器+HTML预览、历史 rehydrate、已中断状态。**478 全绿、独立性 66** |
| 2026-06-28 | **像素前端挂后端 `/app`,一条命令启动整套 demo**：之前演示要开两进程(Vite :5173 + 后端 :8765)+ CORS。现 `npm run build` 出的 `dist/` 由 FastAPI 同源挂在 `/app`(`StaticFiles(html=True)`),前端无进程。vite `base` 在 build 时设 `/app/`(dev 仍 `/`)让资源路径带前缀;前端 API 本就走相对路径(`API_BASE=''`)故同源零 CORS。`/app` 仅在 dist 存在时挂载(裸 checkout/CI 不挂,`/` 回退 `/ui`);`AGENT_ROOM_PIXEL_DIST` 可覆盖路径。`make demo`=build-ui + serve 一条命令。实测:`/`→307→`/app/`、`/app/` 发 index.html、JS 资源 200 同源、healthz 同源可达。**2 新测试**(临时 dist 确定性验证 built/absent 两路径),既有 `/ui` 回退测试 pin PIXEL_DIST 防真实 dist 干扰。**461 全绿、独立性 65** |
| 2026-06-28 | **跨会话记忆默认接入服务器（v0.5 build 早已完成，此为 live 启用）**：之前 `RoleBindings.memory` 默认 `NoOpMemoryProvider`——能力建好但服务器里是关的。现服务器默认绑 `FileFtsMemoryProvider`（`AGENT_ROOM_MEMORY=1`，可关），developer 多得 `memory` 工具。**一致性关键**：bindings 渲染 system-prompt block 的 provider 与 registry 里 `memory` 工具写入的 provider **必须同一实例**（否则本次写下次读不到）——`create_app` 构造一次 `build_server_memory(settings)` 注入两处，测试 `tool.provider is mem` 钉死。spec 动态给 dev 加 `memory`（preset 不列）；shell 关时 memory 仍在。`/healthz.tool_envelope` 加 `memory_enabled`。**8 新测试**（provider 选择、spec/registry 加工具、共享实例、envelope、shell-off 共存）。SECURITY.md 记跨租户/落盘约束 |
| 2026-06-28 | **服务器默认图改用工具型 developer（`full_react`）+ 安全沙箱**：默认 `full`→`full_react`，developer 获 `glob/read_text/write_text/shell` 四工具，让像素 UI 的工具徽章默认就亮（之前纯文本 developer 永不发 `tool_call`）。**安全围栏**集中在 `server/react_runtime.py` 并在 `/healthz.tool_envelope` 暴露：(1) fs 工具锁在 `AGENT_ROOM_WORKSPACE`（`resolve_within_root` 拒 `..`/绝对路径/symlink 逃逸）；(2) shell 白名单 deny-by-default（默认 `pytest,python,python3,ruff`，空串=禁用，且服务器同步把 `shell` 从 spec 删掉防止引用缺失工具）；(3) shell cwd pin 到 workspace；(4) 30s 超时；(5) `max_dev_rounds` 限 ReAct 轮数。`AGENT_ROOM_TOOL_MODE`=unrestricted/approval/read_only 可配。**真 LLM 实测（DeepSeek-V4-Pro）**：默认图发出 5×`tool_call`(glob→read×2→write→shell pytest exit 0)+5×`tool_result`+8×`usage`，沙箱里真把 `a-b` 改成 `a+b`、pytest 真绿。**20 新测试**（spec/registry 一致性、workspace 创建、traversal 拒绝、cwd pin、envelope、env 解析），**451 全绿、独立性 65、ruff clean**；pytest `testpaths=["tests"]` 防 example 沙箱污染收集 |
| 2026-06-23 | **像素风多 agent 前端集成（`mulit_agent_web_ui`，React19+Vite+TS+zustand）**：对齐前端原型到后端契约——(1) api.ts body：`{task}`→`{title,description}`(单 textarea 首行作 title)、resume `{response}`→`{decision}`；(2) 端口 8000→8765；(3) **消费新事件**——types/sse/store 补 `tool_call`/`tool_result`/`usage`/`task_error`（agent 工具状态 + 能量条 token 计数 + 时间线），让前端能演"developer 敲终端跑 pytest"。前端 **11 测试通过**（+3 新）、tsc 零错误、production build 成功；**端到端通线实测**（真后端+DeepSeek）：healthz/CORS=*、`POST /tasks/stream` 收 `{title,description}` 返 `text/event-stream`、事件流 `task_started→node_start→token` 正是前端解析格式。**像素渲染接入**：PixelAgent 加工具徽章（⌨ + 动画终端屏,agent 用工具时显示"跑命令/读文件"）+ token 能量条；EventTimeline 加工具/错误图标；styles.css 像素风新样式 |
| 2026-06-23 | **前端对齐:补齐 SSE 契约缺口（为像素风多 agent UI）**：(1) **tool 事件**——`events.py` 接 `on_tool_start`/`on_tool_end` 发 `tool_call`/`tool_result`（role 从 `<role>_tools` 节点推断），让前端能演"developer 敲终端跑 pytest"。(2) **usage 事件**——`on_chat_model_end` 发 `{input_tokens, output_tokens}` 给实时成本/能量条。(3) **CORS 中间件**——`AGENT_ROOM_CORS_ORIGINS` 可配，默认 `*`，独立前端跨域可用。完整 SSE 事件契约写进 events.py docstring（前端对齐基准）。5 新测试（3 事件 + CORS + 无 usage 跳过），**437 全绿，独立性 64** |
| 2026-06-23 | **v1.0 收口（清单 A+B）**：A 可投化——portfolio §5「站在 2026 标准上:OTEL+MCP」+ 多 agent 精确定位写进 README/portfolio + 校准过期措辞（§0「砍掉 OTEL」、README roadmap）。B structlog 结构化日志（`agent_room/obs/logging.py`，CLAUDE.md §3.5 遗留）——安静默认 + INFO/JSON opt-in + **processor 注入 OTEL trace_id/span_id**（traces↔logs 关联）+ service.run task.start/complete；structlog 进 deps。**432 全绿，独立性 64**。可观测三件套补到 traces+logs |
| 2026-06-23 | **MCP 客户端 adapter（接入 2026 工具集成标准生态）**：`agent_room/tools/mcp.py` `load_mcp_tools` / `register_mcp_tools`——连 MCP server → 工具加载成 LangChain BaseTool → 注册进 Registry,任何角色像内置工具一样用。借 langchain-mcp-adapters,自己只做薄 registry 桥(ADR-0007)。**可选依赖** `pip install 'agent-room[mcp]'`(lazy import,核心不变重)。离线测试:tests/`_mcp_test_server.py` FastMCP 起 stdio 子进程,真 MCP 往返加载+调用 `add` 工具。430 全绿,独立性 63 |
| 2026-06-23 | **OpenTelemetry 追踪落地（2026 行业第一大缺口，调研后从"砍掉"捡回）**：`agent_room/obs/tracing.py`——零影响默认（无 provider 时 no-op，不强制任何外部服务）。埋点：service.run 根 span `agent_room.task` + 每节点 `node.<role>` span（graph 层 `traced_node` 包装,7 角色全覆盖）+ transport `llm.invoke`/`llm.structured` span（GenAI semconv `gen_ai.*` + `agent_room.role`/token/`structured_fallback` 属性）。`AGENT_ROOM_TRACE=console` 经 CLI callback 开箱启用 console exporter。3 离线测试（in-memory exporter 断言 span 树）+ opentelemetry-api/sdk 进 deps。429 全绿，独立性 62 |
| 2026-06-23 | **EVAL-1 收官：clean 13/13 coding 套件**：F1/F2/F3 全修后，13 个 CodingTask 默认 DeepSeek-V4-Pro 零 env 覆盖全跑通——**13/13 (100%) completed 无崩溃**（216894 tokens，`snapshots/eval/coding/summary.json`）。跨场景可靠性可投证据。§8 计划表 EVAL-1/EVAL-2/WRITE-1/HYG-1 全部 ✅ |
| 2026-06-23 | **EVAL-2 two-call review 轴（escalation 三向对比）**：`escalation_two_call` 变体（`two_call_review` preset，reviewer 先 focus-check）加进 planner_gate 套件成三向。真跑：**baseline 25%（0/3 升级）< two_call 75%（2/3）< planner_gate 100%（3/3）**——两种 escalation 策略都 3x 击败 baseline，前置在 planner 的 gate 略胜 reviewer 双调（two_call 在 cache 任务 invented-and-shipped 漏问）。EVAL-2 实质完成 4 个有意义轴（planner_gate/context/memory/two-call）；transport 非真消融（仅一实现，无对照）。426 全绿 |
| 2026-06-23 | **EVAL-2 memory 消融 + 修复 memory 生命周期 gap**：上手即发现真 gap——`bindings.memory.initialize()` 在 service/graph 流程**从没被调用**，配了 FileFts 也静默返回空（memory 开箱不工作，只 memory_recall.py 手动 init 过）。**修复**：`graph.py::build_with_sqlite_checkpointer` 拥有 provider 生命周期（enter init root_dir=db 同级/db_path=同库 + exit close），NoOp 默认 no-op 零行为变化 + 回归测试。**消融**（`evals/memory_tasks.py` 秘密标记只存 memory + 隐藏测试 developer 看不到）：**memory_fts PASS（召回标记 exit=0）vs memory_noop FAIL（不知道 exit=1）**——严格由 memory 决定；端到端坐实 lifecycle 修复；memory 还省成本（20K/87s vs 50K/133s，NoOp 不知答案 churn）。4 离线测试，426 全绿，独立性 60 |
| 2026-06-23 | **token 压力触发（引入 tiktoken，借鉴 hermes `threshold_tokens`）**：`agent_room/context/tokens.py`（tiktoken o200k_base + chars/4 软回退）；`WindowedContextEngine`/`SummaryContextEngine` 加 `max_tokens` 模式——按 token 计数触发而非 message-count，token 界定的 tail（`target_ratio` 默认 0.5，保证幂等）。**修掉瞎触发**：高 message-count 低 token 的小对话不再压缩。`max_messages` 模式保留给离线确定性测试。7 离线测试 + tiktoken 进 deps，418 全绿，独立性 59。**真 LLM 4-way 验证**（NoOp/Windowed/Summary-msg/Summary-token，多文件任务）：Windowed FAIL（37 churn），Summary-msg PASS 但**过度压缩**（40K tok/110s），**Summary-token PASS 且最省**（21K tok/49s/22 msg）——token 触发既正确又比 msg 触发省一半 token、快 2.2x。**阈值改为相对模型上下文窗口的百分比**（`agent_room/llm/context_window.py` 模型→窗口查表 + `compression_budget(model, percent)`，hermes `threshold_tokens ≈ window × percent` 形状，默认 0.5）；eval demo 用 0.05 强制小任务触发，生产 0.5。+4 离线测试，422 全绿，独立性 60 |
| 2026-06-23 | **EVAL-2 context 消融（首次真 LLM 跑 v0.4 上下文引擎）+ dev_messages 遥测**：多文件任务 `fix_pipeline_multifile` + `windowed_ctx` 变体 + `context` suite（NoOp vs Windowed）。**真实发现**：`WindowedContextEngine`(max_messages=8) 在探索型任务上**主动有害**——baseline(NoOp) PASS/16 dev_messages，windowed **FAIL/38 dev_messages**（ReAct 轮数 2.4x，token 更多）。机制：丢中段=丢工作记忆 → developer 反复重探索、churn 到失败。**3-way 验证（NoOp/Windowed/Summary）**：Windowed FAIL（2 次复现 38/35 dev_messages），**Summary（hermes 式摘要中段）PASS/27 dev_messages——"摘要而非丢弃"是解药**。caveat：Summary 对放得下的任务用 token 最多（33K vs 18K），说明压缩非免费、该按 token 压力触发而非低 message-count 瞎触发（hermes 用 token 阈值）。落地：docs/context.md 实测警告 + WindowedContextEngine docstring 警告 + portfolio 收录 |
| 2026-06-23 | **跨场景实证 + F3 修复 + token 补齐**：12 任务 coding 套件默认 DeepSeek-V4-Pro 零覆盖跑出 **11/12→修后 12/12**；抓到并修 F3（`transport.structured` 在 `with_structured_output` 返回 None 时崩 `.model_dump()` → 改为触发 JSON 回退，保证非 None 或抛）；EVAL-1c token 缺口经 `get_usage_metadata_callback` 补齐（纯管道也采集，真测 1935/2579/4514）；transport 按 (role, model) 缓存结构化输出能力，重复调用跳过白打的 primary |
| 2026-06-23 | **F2 修复** 结构化输出在思考模型上经 transport JSON-prompt 回退**开箱即用**（reviewer + planner gate，零 env 覆盖；真测 DeepSeek-V4-Pro completed/approved）|
| 2026-06-23 | **WRITE-1** 作品集叙事 [docs/portfolio.md](docs/portfolio.md)（planner_gate 25%→100% + F1 find-and-fix + 三个反直觉判断）|
| 2026-06-23 | **EVAL-2 首格** planner_gate 消融：baseline 25% vs planner_gate 100%（第一张真实消融表）|
| 2026-06-23 | **EVAL-1** harness（`agent_room/eval/`）+ 12 CodingTask + 正确性 oracle（沙盒+pytest+隐藏测试）|
| 2026-06-23 | **F1 修复** 被拒工具命令回灌 LLM 而非崩溃（eval 发现→离线复现→修→双验证）|
| 2026-06-23 | **v1.0 重定向**为作品集证明（砍生产化栈）；§5.7 scrubber wiring；CLAUDE.md trim 344→219；transport 抽象 ADR-0013；仓内薄 UI ADR-0012 |
| 2026-06-22 | v1.0 §6.1 sessions 薄壳 + ADR-0011 状态映射 + `/healthz` role 映射 |
| 2026-06-14 | v0.5 跨会话记忆（两层+一工具+冻结快照 ADR-0010）；mypy strict + coverage 93% |
| 2026-06-13 | v0.4 上下文工程（3 引擎 + spill + 分层 prompt）；DeepSeek 真 LLM 实测 |
| 2026-06-12 | v0.3 工具系统（4 内置工具 + Registry + 3 权限模式 + ReAct + approval）；独立性运行时硬测 |
| 2026-06-12 | v0.2 DAG 配置化（GraphSpec + 5 preset + escalation lab；真测 planner_gate 67%）|
| — | v0.1 核心骨架（4 角色 + LangGraph + AsyncSqliteSaver + FastAPI/SSE + Typer CLI，63 测试）|

**已知缺口**：长任务 SSE 帧节流；DeepSeek 偶发空 `tool_use` block（已用
`retry_on_parser_error` 缓解）；OpenAI 结构化输出未在真 provider 上验证（DeepSeek
思考模式的 tool_choice 拒绝 + 返回 None 已由 transport JSON 回退修复，F2/F3；
transport 按 (role, model) 缓存能力，每任务每节点只白打一次 primary 而非每轮）。

**v1.0 收口清单**（2026-06-23 刷新——计划内 v1.0 主体已完成，下面是收口 + 候选）：

> 已完成：§8 四件套（EVAL-1/2 / WRITE-1 / HYG-1）、§5.7 scrubber、OTEL 追踪、
> MCP adapter、EVAL-2 四轴消融、压缩全链（摘要→token 触发→percent-of-window）。

**A. 收口·可投化**（让已做的活被面试官看见）✅ done 2026-06-23
- [x] A1 portfolio §6：OTEL + MCP 写进可投证据
- [x] A2 校准过期措辞：§0「明确砍掉 OTEL」已做 / multi-agent 精确定位
- [x] A3 README：features 补 OTEL / MCP / 多 agent 定位 + roadmap v1.0 校准

**B. 可观测三件套补完**（接 OTEL，traces→logs）✅ done 2026-06-23
- [x] B1 structlog 结构化日志（CLAUDE.md §3.5 遗留）——`agent_room/obs/logging.py`：安静默认
  (WARNING)，`AGENT_ROOM_LOG=info`/`configure_logging` 开 INFO/JSON；processor 注入当前
  span 的 `trace_id`/`span_id`（**与 OTEL trace 关联**）；service.run 绑 task_id + 出
  `task.start`/`task.complete`。2 离线测试，结构化 JSON 日志实测带 trace_id

**C. 2026 前沿 Tier-2**（新 scope，需设计；不阻塞收口）
- [ ] C1 episodic memory（历史 trajectory 指导新任务）
- [ ] C2 记忆生命周期（consolidation / forgetting）
- [ ] C3 guardrail 四点层（input / tool-call / tool-response / output）

**受阻 / 推迟**：OpenAI provider 真测（缺 creds）；Postgres checkpointer / Prometheus
metrics / PyPI 打包（v2.x 可选）。
---

## 1. 路线图总览

```
v0.1 (DONE) ──▶ v0.2 DAG 配置 ──▶ v0.3 工具系统 ──▶ v0.4 上下文工程 ──▶ v0.5 跨会话记忆 ──▶ v1.0 GA ──▶ v1.x 生态/能力补强 (✅ 全部完成)
   核心骨架        YAML 驱动         自研 Tool Registry    自研 ContextEngine    自研记忆 + FTS5     稳定 + UI    MCP/预算/护栏/向量记忆/Qdrant/租户隔离
```

**v1.x 状态**：§6.9 gap-closing roadmap（MCP §6.9-1 / 预算 §6.9-2 / guardrail
§6.9-3 / 混合语义记忆 §6.9-4）四项 + 追加的 §6.15（Qdrant 可插拔向量后端 +
物理级租户隔离）**全部完成**；北极星三条出口标准（EVAL / WRITEUP / 可读性）
也全部达成——项目已经是"可投"状态,下一步是选择性加分项,不是必须项,
见 §6.9.4 / §7.4 剩余条目。

**自研 = 借鉴 hermes-agent 的设计模式，在 agent-room 仓库内独立实现。** 不 import hermes-agent。

每个里程碑必须满足三个出口标准：
1. **测试**：新增功能有覆盖率，`pytest -q` 全绿。
2. **文档**：CLAUDE.md / README 同步更新。
3. **示例**：examples/ 至少加一个 PoC。
4. **独立性**：删除 `/home/ly/hermes-agent/` 后本项目仍正常工作（CI 里加这条断言）。

---

## 2. v0.2 — DAG 配置化 ✅ 已完成（2026-06-12 收尾，归档于 [docs/archive/v0.2-graphspec.md](docs/archive/v0.2-graphspec.md)）

GraphSpec 落地 + 5 个预设 + escalation lab。10 个子任务全 ✅，63/63 离线测试。
真实 LLM 实测：planner_gate 67% / two_call_review 33–100% / baseline 0%
（详见 [findings F2 §"v0.2 §2.9"](docs/findings/2026-06-11-smoke-v0.1.md)）。
F2 修复结论已合入 [ADR-0002](docs/adr/0002-reviewer-protocol.md)。

**v0.2 留给 v0.3+ 的注意事项**（详见归档）：
- `with_structured_output` 路径要容忍 provider 偶发 `OutputParserException`（DeepSeek 实测 3/9）
- escalation 实验要做成 GraphSpec 变体而不是一次性脚本（v0.3 工具 A/B 沿用）

---

## 3. v0.3 — 工具系统 ✅ 已完成（2026-06-12 收尾，详见 [ledger](docs/archive/ledger-v0.x.md) v0.3.x 落地条目）

### 3.1 动机

让 developer 能在节点内跑 shell / 读写文件 / 调 HTTP。

**借鉴 hermes-agent 的 [tools/registry.py](../hermes-agent/tools/registry.py) 设计**：
- `ToolEntry` dataclass + `__slots__`
- AST 扫描自动发现并注册（不强制 import time 副作用）
- `RLock` + 快照读保证并发安全
- 大输出落盘，state 里只存 `<persisted-output>` 占位

**这些都在 agent-room 仓库内重写**，不 import hermes-agent。
LangChain 已有 `BaseTool` / `ToolNode`，骨架直接复用 LangChain 抽象，
只补「Registry + 权限模式 + 大输出 spill」这三件 hermes 风格的能力。

### 3.2 设计

每个角色可声明 `tools: list[BaseTool]`：

```python
RoleBindings(
    developer=ChatAnthropic(model="claude-opus-4-7"),
    developer_tools=[ShellTool(allowlist=["pytest", "npm test"]), ReadFileTool(), WriteFileTool()],
)
```

实现路径：
1. 角色节点改成 LangGraph 子图：`agent → tools → agent`（标准 ReAct）。
2. `agent_room/tools/registry.py` —— ToolEntry + 自动发现（**借鉴 hermes 模式，本仓库自研**）。
3. `agent_room/tools/spill.py` —— 大输出落盘到 SQLite/文件，prompt 内只保留占位（**借鉴 hermes 模式，本仓库自研**）。
4. 工具调用结果同步追加进 `state.events`。

### 3.3 任务拆分

| # | 任务 | 文件 | 估时 |
|---|------|------|------|
| 3.1 | 内置 4 工具：shell / read / write / glob | `agent_room/tools/` (新) | 2d |
| 3.2 | Tool Registry（借鉴 hermes，本仓库自研） | `agent_room/tools/registry.py` | 1d |
| 3.3 | 工具白名单 + 权限模式（read-only / approval / unrestricted） | `agent_room/tools/policy.py` | 1d |
| 3.4 | 大输出落盘（借鉴 hermes 的 tool_result_storage） | `agent_room/tools/spill.py` | 1d ✅ done 2026-06-13 (v0.4) |
| 3.5 | 角色子图：agent ↔ tools | `agent_room/roles/_react.py` | 2d |
| 3.6 | 测试：mock 工具 + 多轮调用 | `tests/test_tools.py` | 1d |
| 3.7 | 安全测试：路径越权 / 命令注入 | `tests/test_tool_security.py` | 1d |
| 3.8 | 文档：工具开发指南 | `docs/tools.md` | 1d |
| 3.9 | examples：让 developer 真的运行 pytest | `examples/dev_runs_tests.py` | 1d |

**出口标准**：
- developer 能跑 `pytest tests/` 并把结果回灌给 reviewer。
- 工具调用的安全模式默认是 `approval`（每次都问），不是 `unrestricted`。
- `ls /home/ly/agent_room/tools/` 至少 5 个文件，全部不依赖 hermes-agent。

### 3.4 风险

- **R3**：shell 工具是高危点，必须默认 deny-by-default + 显式 allowlist。
- **R4**：tool 输出污染 prompt → 用 spill + 占位符截断。

---

## 4. v0.4 — 上下文工程 ✅ 已完成（2026-06-13 收尾，含 §3.4 spill 接入与 DeepSeek 真 LLM 实测，详见 [ledger](docs/archive/ledger-v0.x.md) v0.4 + v0.4.x 落地条目）

### 4.1 动机

长对话 / 多轮 ReAct 会撑爆 token 预算。

**借鉴 hermes-agent 的** [agent/context_engine.py](../hermes-agent/agent/context_engine.py) **+** [agent/context_compressor.py](../hermes-agent/agent/context_compressor.py)：
- `should_compress` / `compress` 两阶段决策
- `protect_first_n=3, protect_last_n=6` 的保护窗口（保护任务定义和最近 turn）
- 压缩到固定预算时分级降级（先丢工具结果，再合并对话，再摘要）
- 提示词缓存不变量：临时注入永远不进 system prompt（[prompt_builder.py](../hermes-agent/agent/prompt_builder.py)）

**全部在 agent-room 仓库内自研**，定位是 LangGraph 节点的"上下文管理层"。

### 4.2 设计

```python
# agent_room/context/engine.py — 自研，模式来源 hermes-agent
class ContextEngine:
    def __init__(self, max_tokens: int, protect_first: int = 3, protect_last: int = 6): ...
    def should_compress(self, messages: list[BaseMessage]) -> bool: ...
    async def compress(self, messages: list[BaseMessage]) -> list[BaseMessage]: ...
```

`RoleBindings` 加 `context_engine: ContextEngine | None`，节点调 LLM 前先过 engine。

加 `agent_room/prompt/builder.py` —— **自研** 的分层 prompt 装配（借鉴 hermes 10 层模式但精简到本项目需要的 4-5 层）。

### 4.3 任务拆分

| # | 任务 | 文件 | 估时 |
|---|------|------|------|
| 4.1 | `ContextEngine` 接口 + 默认实现（不压缩） | `agent_room/context/engine.py` | 1d |
| 4.2 | `WindowedContextEngine`（保护窗口 + 截断） | `agent_room/context/engine.py` | 1d |
| 4.3 | `SummaryContextEngine`（用小模型做摘要） | `agent_room/context/engine.py` | 2d |
| 4.4 | 分层 prompt 装配器（借鉴 hermes prompt_builder） | `agent_room/prompt/builder.py` | 2d |
| 4.5 | 节点接入：调 LLM 前先过 engine | `agent_room/roles/_react.py` | 1d |
| 4.6 | 测试：长对话触发压缩、保护窗口生效 | `tests/test_context.py` | 2d |
| 4.7 | 文档：上下文管理设计说明 | `docs/context.md` | 1d |
| 4.8 | examples：长任务自动压缩 PoC | `examples/context_compression.py` | 1d |

**出口标准**：
- 一个长达 50 轮的 ReAct 任务能在固定 token 预算内完成。
- 删除 `/home/ly/hermes-agent/` 后，本节功能完全不受影响。

### 4.4 风险

- **R5**：摘要会丢信息 → 关键工具结果走 spill（v0.3 已落盘），摘要里指向占位 ID。
- **R6**：压缩节奏太激进 → 加测试断言保护窗口内消息逐字保留。

---

## 5. v0.5 — 跨会话记忆（目标：3 周）— ✅ 已完成（2026-06-14，详见 [ledger](docs/archive/ledger-v0.x.md) v0.5 落地条目 + [ADR-0010](docs/adr/0010-memory-architecture.md) + [docs/memory.md](docs/memory.md)）

### 5.1 动机

LangGraph checkpointer 解决了"单任务跨进程恢复"，但没有"跨任务、跨会话的事实/偏好记忆"。

**借鉴 hermes-agent 的**：
- [agent/memory_provider.py](../hermes-agent/agent/memory_provider.py) — `MemoryProvider` ABC
- [tools/memory_tool.py](../hermes-agent/tools/memory_tool.py) — 暴露给 LLM 的 add/recall 工具
- [hermes_state.py](../hermes-agent/hermes_state.py) — SQLite + FTS5，`truncate_around_matches`

**全部在 agent-room 仓库内重新实现**。

### 5.2 设计（实际落地版本）

> 原 §5.2 把 memory 想成"单层 + remember/prefetch"，落地时发现 hermes 真正在用的是**两层 + 一工具 + 冻结快照**——curated 是 LLM 主张的事实（小、稳定、读路径冻结），transcript 是历史对话搜索（大、增量、按 query 召回）。一个 schema 装不下两种语义，强行合并对 LLM 极不友好。详细决策见 [ADR-0010](docs/adr/0010-memory-architecture.md)。

```python
# agent_room/memory/provider.py
class MemoryProvider(Protocol):
    async def initialize(self, **kwargs) -> None: ...
    async def system_prompt_block(self) -> str: ...           # 冻结快照
    async def prefetch(self, query: str, k: int = 5) -> str: ...  # transcript 检索
    async def sync_turn(self, role, content, *, tool_call_id=None, tool_name=None) -> None: ...
    async def close(self) -> None: ...
```

实现：
- `NoOpMemoryProvider`（默认绑到 `RoleBindings.memory`，零行为变化）
- `FileFtsMemoryProvider`（v0.5 默认后端）= `CuratedFileStore` + `TranscriptStore`
  - `CuratedFileStore` → `<root>/memory/MEMORY.md` (2200 cap) + `USER.md` (1375 cap)
  - `TranscriptStore` → SQLite + FTS5（外部内容虚拟表，跟 LangGraph checkpointer 共库不冲突）

工具暴露给 LLM：单一 `memory(action, target, content, old_substring=None)`——`recall` **故意不暴露**（auto-prefetch 每轮自动跑，少一个工具决策更稳）。

### 5.3 任务拆分（实际落地版本）

| # | 任务 | 文件 | 状态 |
|---|------|------|------|
| 5.1 | `MemoryProvider` Protocol + `NoOpMemoryProvider` + `Memory` schema + `RoleBindings.memory` 字段 | [`agent_room/memory/provider.py`](agent_room/memory/provider.py) | ✅ done |
| 5.2 | `CuratedFileStore`（MEMORY.md + USER.md，原子写、字符上限、11-pattern 威胁扫描、frozen snapshot） | [`agent_room/memory/curated.py`](agent_room/memory/curated.py) | ✅ done |
| 5.3 | `TranscriptStore`（SQLite + FTS5 + 三触发器同步 + LIKE/CJK fallback + OR 重写 + stopword） | [`agent_room/memory/fts.py`](agent_room/memory/fts.py) | ✅ done |
| 5.3.1 | `FileFtsMemoryProvider` 把两层拼成 v0.5 默认后端 | [`agent_room/memory/file_fts.py`](agent_room/memory/file_fts.py) | ✅ done |
| 5.4 | `MemoryTool`（action × target dispatch，opt-in 注册到 ToolRegistry） | [`agent_room/memory/tool.py`](agent_room/memory/tool.py) + [`agent_room/tools/__init__.py`](agent_room/tools/__init__.py) | ✅ done |
| 5.5 | developer_react 接入：第一次 entry 注入 curated + `<memory-context>` 围栏 prefetch；fire-and-forget `sync_turn` | [`agent_room/roles/developer_react.py`](agent_room/roles/developer_react.py) | ✅ done |
| 5.6 | 跨会话集成测试 + 文档 + ADR + 端到端 demo | [`tests/test_memory_*.py`](tests/) ×5 + [`docs/memory.md`](docs/memory.md) + [`docs/adr/0010-memory-architecture.md`](docs/adr/0010-memory-architecture.md) + [`examples/memory_recall.py`](examples/memory_recall.py) | ✅ done |

**出口标准**（实测落地）：
- ✅ 第一个任务里 LLM 调 `memory.add(...)`，第二个独立任务 LLM 能 recall 到——`tests/test_memory_developer_react.py::test_real_provider_end_to_end_curated_visible` + `examples/memory_recall.py` 真跑 4 项检查全 True
- ✅ 全程不依赖任何外部数据库 / 向量库 / hermes-agent——`grep -rE "from hermes_|import hermes_" agent_room/memory/ tests/test_memory*.py examples/memory_recall.py` 零结果
- ✅ "删除 hermes-agent 后所有测试仍绿"——`tests/_independence_driver.py` 子进程 MetaPathFinder 拦截 `hermes_*`，46 子模块全部能干净导入；CI `independence` job 跑这条断言（v0.5 期间持续绿）

**v0.5 收尾数据**：73 个新测试 / 6 个新模块 / 2 篇文档 / 1 个 ADR / 1 个 demo / **336/336** 全绿 / 独立性 39 → 46 子模块。

---

## 6. v1.0 — 稳定 + UI (目标：6 周)

### 6.1 内容

- **API 冻结**：`AgentRoomService` / `schemas.py` / 路由路径 SemVer 化。
- **仓内薄 UI**（2026-06-23 [ADR-0012](docs/adr/0012-in-repo-thin-ui.md) 撤回 Vue UI 复用方案）：FastAPI + Jinja2 + 单文件 `app.js`，挂在 `/ui`，无 npm 无构建。生产用户用 OpenAPI 自建 SPA。
  > **2026-06-14 形状审计完成** —— 见 [docs/v1.0-vue-ui-reconciliation.md](docs/v1.0-vue-ui-reconciliation.md)。
  > 关键落差：(1) 客户端 session-rooted 而本仓库 task-rooted，(2) 12 状态机 vs 4 状态机，
  > (3) Run/RoleRun/Profile 一等公民 vs 本仓库无对应实体，(4) 客户端纯 3s polling、本仓库已有 SSE。
  >
  > **2026-06-22 D1-D4 拍板**：D1 session 不做一等概念（3 列 tag 表）/ D2 4 状态服务端不动 / D3 SSE only / D4 Profile/RoleBinding 推 v1.1。后端 §6.1.1-§6.1.3 据此落地（sessions 薄壳 + ADR-0011 12→4 状态映射 + `/healthz` role 映射）。
  >
  > **2026-06-23 撤回 Option C，转向仓内薄 UI（[ADR-0012](docs/adr/0012-in-repo-thin-ui.md)）**：
  > 复用 hermes-web-ui 需要"删 40% 视图 + 重写 store + 12→4 状态收敛"，工作量本身已逼近重做；同时使用面留下"必装外部前端仓库"的独立性漏洞。
  > 改成仓内 ~300-500 行 server-rendered 薄 UI（Jinja2 + EventSource）：(a) 工作量从 ~4.5d 降到 ~2.5d；(b) `pip install -e .` 即包含 UI，独立性闭环；(c) 自己写前端反向倒逼 v1.0 API 收敛。
  > [ADR-0011](docs/adr/0011-vue-ui-state-mapping.md) 12→4 状态映射表不变，仓内 UI 沿用同一份契约；hermes-web-ui 不再是 v1.0 依赖（兼容面 `/api/agent-room/*` 仍保留给自建 SPA）。
  > 重估预算：**~2.5 天**（templates+JS 1.5d + routes+test 0.5d + smoke+docs 0.5d）。
- **生产化**：
  - Postgres checkpointer 选项
  - Prometheus 指标（任务数、各 decision 占比、平均轮次、p95 延时）
  - structlog 结构化日志
- **文档完整化**：所有公开 API 有 docstring 段落（破例放宽 CLAUDE.md 注释规则）。
- **CI/CD**：GitHub Actions 跑 lint + pytest + 类型检查。**新增独立性 CI 步骤**：在临时容器里 `rm -rf /home/ly/hermes-agent` 后再跑 `pytest -q`，必须仍全绿。
- **打包**：发到 PyPI / 内部 PyPI。

### 6.2 任务拆分（高层）

| 模块 | 任务 |
|------|------|
| **稳定性** | API 文档冻结、SemVer 检查工具、deprecation 流程 |
| **可观测** | Prometheus exporter、OpenTelemetry traces、structlog |
| **UI**（in-repo thin UI，2026-06-23 ADR-0012）| §6.1.1 sessions 薄壳 ✅ / §6.1.2 ADR-0011 12→4 状态映射 ✅ / §6.1.3 `/healthz` role 映射 ✅ / §6.1.4' 仓内 Jinja2 模板 + 单文件 app.js / §6.1.5' FastAPI UI 路由 + GET 流变体 / §6.1.6' smoke 文档 + 截图 + reconciliation 收尾 — 详见 §8 backlog 6.1.x rows |
| **生产** | Postgres checkpointer、连接池、并发限流 |
| **文档** | 完整 API ref（mkdocs / sphinx）、教程、ADR 全集（含新 ADR-0011 状态机映射） |

---

## 6.9 v1.x — 拉近主流框架差距（gap-closing roadmap，2026-06-29 立项）

### 6.9.1 动机

与成熟框架（CrewAI / AutoGen / OpenAI Agents SDK / 裸 LangGraph）对比后，确认本项目
**不与它们拼"通用性 / 广度"**（必输：生态、规模、社区）。差异化身份是
**"中心化、可控、可评测的多角色协作"**。本节只补两类缺口：

- (a) **靠标准 / 复用就能廉价拿到**的（典型：MCP）；
- (b) **强化"可控 + 可评测"身份**的（预算熔断、guardrail、可消融的向量记忆）。

凡是会把它劣化成"更差的 CrewAI 克隆"的缺口，**故意不补**（见 §6.9.4）。

### 6.9.2 任务拆分（按推荐落地顺序）

| # | 任务 | 关键设计 | 出口标准 | 估时 | 优先级 |
|---|------|---------|---------|------|------|
| 6.9-1 | **MCP 工具接上线** ✅ done 2026-06-30 | [`tools/mcp.py`](agent_room/tools/mcp.py) 的 `load_mcp_tools`/`register_mcp_tools` 已存在但**未接进 server/registry**。env 配 `AGENT_ROOM_MCP_SERVERS`（JSON）→ 启动时注册进 developer registry，纳入同一 approval/allowlist/沙箱围栏；`/healthz.tool_envelope` 报告已挂载的 MCP server。 | "零生态"→"接入整个 MCP 生态"；真实 MCP server（如 filesystem / fetch）联调出 tool_call/tool_result；NoOp（未配置）时零变化；新测试 ≥6 | ~1d | **P0** |
| 6.9-2 | **Token/成本预算 + 熔断** ✅ done 2026-06-30 | 每任务 `max_tokens`/`max_cost`/`max_tool_calls` 预算（已有 usage 事件 + OTEL，缺 enforcer）。超限优雅中止 → `task_error`（budget_exceeded），状态机 halt。env 可配（TaskRequest 覆盖 scope cut，详见 §6.12）。 | 强化"agent 不失控烧钱"叙事；预算超限可复现中止；默认无限（不破现有行为）；新测试 ≥6 | ~1d | **P0** |
| 6.9-3 | **通用 Guardrail 层（4 点式）** ✅ done 2026-06-30 | 统一接口：输入（PII/越狱检测）、输出（schema 校验/敏感过滤）、tripwire 中止、审计。把现有 [`memory/scrubber.py`](agent_room/memory/scrubber.py) + curated 注入扫描收编进来。NoOp-default 可消融。（实际落地为执行阶段 4 点：input/tool_call/tool_response/output，详见 §6.13） | OpenAI Agents SDK/NeMo 对标项；越狱/PII 用例被拦；reviewer 协议不变；新测试 ≥8 | ~2d | **P1** |
| 6.9-4 | **语义/向量记忆（可插拔后端）** ✅ done 2026-07-01 | `TranscriptStore` 加可选 `embedding:` 参数，`sqlite-vec` KNN 与既有 FTS5 并存，reciprocal rank fusion 融合排序。保持 NoOp-default（详见 §6.14）。 | 补齐"无向量召回"硬差距；产出消融数字（FTS5 vs hybrid 命中率，**喂养评测优势**）；新测试 ≥8 | ~2-3d | **P1** |

**推荐顺序**：6.9-1 ✅ → 6.9-2 ✅ → 6.9-3 ✅ → 6.9-4 ✅。**§6.9 gap-closing roadmap
全部完成**——对外叙事：接入 MCP 生态、有成本护栏、有安全 guardrail、混合语义记忆的
**可评测**协作系统，且每一项都有真实服务器+真 LLM 联调证据，不是纸面声明。

### 6.9.3 复用既有 infra

- **MCP**：适配器已写好，主要是 wiring（参照本轮 tools/memory 的 `build_server_*` + env-gate 模式）。
- **预算**：usage 事件（[events.py](agent_room/events.py)）+ OTEL（[obs/](agent_room/obs/)）已采集 token，只缺 enforcer。
- **Guardrail**：scrubber + curated 威胁扫描是现成基础，收编成通用层。
- **向量记忆**：`MemoryProvider` Protocol + NoOp-default 模式直接复用，新增一个后端实现。

### 6.9.4 故意不做（补了反而扣分）

- ❌ **自写几百个工具集成** —— MCP（6.9-1）一招覆盖，自写即劣化版 LangChain。
- ❌ **去中心化 swarm / A2A / 动态子 agent 树** —— 明确非目标（CLAUDE.md §1.2），稀释"中心化可控"核心卖点。
- ❌ **多租户 RLS** —— 大工程、作品集回报低，推到真生产期。
- ❌ **自研编排取代 LangGraph** —— 违反"复用难且通用部分"原则（CLAUDE.md §1.3）。
- ⚠️ **受控并行 fan-out**（多 developer 并行试方案）—— 非 swarm、LangGraph 原生支持，但**仅当**要展示"驾驭更复杂编排"时才做（P2，看目标）。

---

## 6.10 per-task workspace 隔离 — ✅ 已完成（2026-06-29）

> 落地：`open_checkpointer`（saver+memory 开一次）+ `compile_with` 工厂 → 每任务
> `workspace/<task_id>` 现编图；文件接口/preview 按 task_id 限定。465 测试 + 真 LLM
> 双任务实测各落各子目录零冲突。详见 §0 changelog 2026-06-29 行。

### 动机

服务器把工具 registry 在启动时构建**一次**，所有任务共享同一个 `workspace/`，导致：
(1) 任务 B 覆盖任务 A 的同名文件；(2)「生成文件」tab 把所有任务产物混在一起；
(3) 并发任务互相踩文件。对单用户顺序 demo 能用，但严格说是缺陷。

### 设计（tools 不动，按 task 建图）

试过两条"运行时注入 task_id"的路都不干净：RunnableConfig 不把 `thread_id` 透到
`_run`；`InjectedState` 要 checkpointer config 且会改动重测试的 tool schema。故选
**per-task 编译**——工具 root 固定，但每个任务用 `workspace/<task_id>` 重建 registry+graph，
**共享**长命的 checkpointer 连接 + memory provider（这俩仍只初始化一次）：

- `graph.py` 加 `open_checkpointer(bindings, db_path)` CM：开 saver + init memory 一次，
  yield 一个 `compile_with(spec, registry)` 工厂。`build_with_sqlite_checkpointer` 保留给
  CLI/tests 不动。
- `react_runtime.build_server_registry(..., workspace_override=Path)` 支持 per-task root。
- `api.py` lifespan 用 `open_checkpointer`：base graph 给 snapshot 只读；stream/resume 用
  `compile_with(spec, per_task_registry)` 现编。stream 开始时 `mkdir workspace/<task_id>`。
- 文件接口按 task 限定：`/workspace/files?task_id=`、`/workspace/file?task_id=&path=`、
  preview 路径含 `<task_id>`。前端 FileBrowser 传当前 task_id。

### 出口标准

任务 A、B 各写 `index.html` 互不覆盖（各在自己子目录）；「生成文件」只显示**当前任务**的
产物；点历史任务看到的是**它自己**的文件；`../` 逃逸仍 400；既有 464 测试不破 + 新测试
≥4（隔离、按 task 列表、历史 task 文件、traversal）。

---

## 6.11 SSE 断线重连 + 续传 — ✅ 已完成（2026-06-29）

> 落地：`RunManager`（后台 detached run + seq-tagged 环形缓冲 + subscribe 重放/tail）；
> `POST /tasks/stream` 改 detached + `subscribe(-1)`；`GET /tasks/{id}/events?from=N`
> 重连（无 live run→409→前端快照兜底）；前端 `readSse` 抓 id、`streamTask` 退避重连。
> **真 LLM 实测**：跑到 planner 时掐断 6s，任务后台跑完，`?from=1` 重连从 id=2 续到
> task_finished、status=completed。RunManager 8 单测 + 2 api resume 测试，475 全绿、独立性 66。

### 动机

当前 `POST /tasks/stream` 把图运行直接绑在 SSE 连接上：客户端一断（刷新/网络抖动/
代理超时），驱动 `service.stream` 的生成器被取消 → **整个任务运行中止**（这也是
"任务卡 running 无产出"的根因）。要做到"断线不丢进度、重连续传",必须让运行**脱离
连接**，并能从断点重放事件。

### 设计（detached run + 可重放事件缓冲）

- `agent_room/server/runs.py` 新增 `RunManager`：`start(task_id, produce)` 把
  `produce()`（格式化 SSE 帧的异步生成器）丢进 **后台 asyncio.Task** 跑，事件按
  seq 写进每任务环形缓冲；客户端断开**不影响**后台跑完。`subscribe(task_id,
  last_event_id)` 从 `last_event_id+1` 重放缓冲、再 tail 直到 done。
- `POST /tasks/stream`：改成 `run_manager.start(...)` + 返回 `subscribe(-1)`。每帧带
  `id: <seq>`。运行脱离连接。
- `GET /tasks/{id}/events?from=N`：重连入口，`subscribe(N)`。无 live run（已跑完淘汰/
  服务器重启）时返回 409，前端回退到既有快照恢复。**与 thin-UI 的
  `/tasks/{id}/stream` 不冲突**（独立路径）。
- 前端：`readSse` 捕获 `frame.id` → `lastEventId`；`streamTask` 流断且任务未终态时,
  退避重连 `GET /events?from=lastEventId` 续上，去重靠 seq。重连耗尽 → 既有恢复兜底。

### 出口标准

任务跑到一半 curl 中断连接 → 后台跑完（快照 completed）；带 `from=N` 重连拿到 N 之后
的事件 + task_finished；happy path 客户端行为不变（465 测试 + 真 LLM 不破）；RunManager
单测 ≥6（缓冲 seq、重放、并发订阅、done 后订阅、缓冲上限、未知 task）。

---

## 6.12 §6.9-2 Token/成本预算 + 熔断 — ✅ 已完成（2026-06-30）

> 落地：`agent_room/budget.py` 新模块（`Budget`/`BudgetTracker`/`BudgetExceededError`，
> 独立于 `agent_room/llm/` 不占 ADR-0013 的文件/行数封顶）；`graph.py` 每次
> `build_uncompiled_from_spec` 建一个共享 `LangChainTransport(bindings,
> budget=BudgetTracker(bindings.budget))`，通过既有的 `transport=` 注入点（7 个角色
> factory 早就有）传给每个节点，同一次任务的用量累计在一个 tracker 里；超限从
> `invoke()` 内部抛 `BudgetExceededError`，节点不捕获、自然往外传，被 `RunManager`
> 通用异常兜底接住转成 `task_error(budget_exceeded=true)` 帧（复用 §6.11 机制，零
> 新服务层代码）。`RoleBindings.budget`/`Settings.budget()` NoOp-default；
> `AGENT_ROOM_MAX_TOKENS`/`MAX_TOOL_CALLS`/`MAX_COST_USD`/`PRICE_PER_1K_*` 环境变量；
> `/healthz.budget` 报告当前天花板。**两处诚实 scope cut**（只做 env 级非
> per-TaskRequest；budget 累加器不进 checkpointer 故 resume 后清零）已写进
> SECURITY.md + 本节 + 模块 docstring，非事后补救。**中途发现并修的一处设计缺陷**：
> `structured()` 的"preflight-only 检查"最初设计成"拦截已经超限的状态"，但逻辑推演
> 后发现这个状态在当前架构下**不可达**（累加调用一旦越界会立刻自己抛异常，不存在
> "静默越界"这个中间态）——发现后直接删除死代码而非留着凑数，诚实记进模块 docstring
> 的"Known scope limit"。**真 LLM 实测**：`AGENT_ROOM_MAX_TOKENS=50` 起服务器,
> `/healthz.budget` 正确报 `enabled:true, max_tokens:50`；真跑一个任务,planner 第一次
> LLM 调用耗 775 token 立刻越界,SSE 收到 `task_error` 帧且
> `budget_exceeded:true, budget_dimension:"max_tokens", budget_limit:50,
> budget_actual:775`（graph 层同一逻辑此前已用 fake usage LLM 验证过，这次是端到端
> 真实 DeepSeek-V4-Pro 复现）。**19 新测试**（tracker 记账/NoOp/超限 10 + transport
> 集成 5 + graph 级真实中止 2 + RunManager 终态帧 1 + healthz 上报 1），
> **508 全绿、独立性 67、mypy strict 0 错**。

### 动机

agent 失控烧钱是多角色协作系统最现实的运营风险——developer 的 ReAct 工具环没有
硬上限就可能在坏 prompt/坏工具反馈下反复调用 LLM。已有 usage 事件 + OTEL 采集了
token 数，但没有人在超限时真的**停下来**。

### 设计要点（选定方案 + 两处诚实的 scope cut）

**插入点选择**：没有走"state 里存累计用量 + 每个节点自己检查"（会在 7 个角色文件
里重复预算判断逻辑，正是 ADR-0013 建 transport seam 想消灭的重复），也没有走"改
`Transport.invoke` 签名塞 state 进去"（破坏性契约变更）。选择**`LangChainTransport`
内部持一个 `BudgetTracker`**：`invoke()` 每次调用后记录 usage + tool_calls 数、
超限就抛 `BudgetExceededError`；`structured()` 调用前先做同样的阈值检查（reviewer/
planner_gate 走 `with_structured_output` 拿不到 usage_metadata，这是 LangChain
现有限制，不在本次范围内解决——但预算仍能拦住"developer already 超了、下一个
structured 节点别再跑"这一步）。异常从节点里自然往外传，被 `RunManager._drive`
的通用异常兜底接住（复用 §6.11 刚建的机制），转成终态 `task_error(budget_exceeded=
True)` 帧——**零新增服务层逻辑**，完全复用已有的 detached-run 错误传播路径。

**新模块 `agent_room/budget.py`**（不放进 `agent_room/llm/`）：ADR-0013 明确把
transport 抽象封顶在"≤3 文件、≤300 行"，budget 是 transport 的*消费方*而不是
transport 本身的一部分，放独立模块避免把那个封顶算糊。`Budget`（冻结配置：
`max_tokens` / `max_tool_calls` / `max_cost_usd` / `price_per_1k_input` /
`price_per_1k_output`）+ `BudgetTracker`（可变累加器，`budget=None`→NoOp，
镜像 `ContextEngine`/`MemoryProvider` 的 NoOp-default 形状）+ `BudgetExceededError`。

**graph.py**：`build_uncompiled_from_spec` 顶部建**一个**共享
`LangChainTransport(bindings, budget=BudgetTracker(bindings.budget))`，通过
`_instantiate_node` 传给每个角色 factory 的 `transport=` 参数（这个参数所有 7 个
factory 早就有，是已有的注入点，零新签名）——保证同一次任务的 4+ 个角色节点
共享同一个累加器，而不是各自建一个从零计数。

**config**：`RoleBindings.budget: Budget | None = None`（NoOp 默认，不破现有行为）；
`Settings` 新增 `AGENT_ROOM_MAX_TOKENS` / `AGENT_ROOM_MAX_TOOL_CALLS` /
`AGENT_ROOM_MAX_COST_USD` / `AGENT_ROOM_PRICE_PER_1K_INPUT` /
`AGENT_ROOM_PRICE_PER_1K_OUTPUT`，全部留空默认无限。

**scope cut #1（诚实记录）**：原 PLAN 措辞"env/TaskRequest 可配"——本次**只做
env 级**（运维在部署时定一个统一天花板），不做 per-`TaskRequest` 覆盖。原因：
per-request 覆盖要把预算一路从 `TaskRequest` 穿透 `compile_with`/
`build_uncompiled_from_spec`/`_instantiate_node` 的公开签名，屏蔽面已经超过
一个"~1d"任务该碰的范围，且当前没有真实调用方需要"这次任务的预算比默认高/低"。
按 CLAUDE.md §6.3"不要把灵活性当理由加抽象层"搁置，真需要再开新任务补。

**scope cut #2（诚实记录）**：budget 累加器活在 `LangChainTransport` 实例里（不进
`TaskState`/checkpointer），所以**跨 resume 不连续**——`need_user_decision`/
tool-approval 中断后 `resume()` 会重新 `compile_with()` 建一张新图、拿到一个
全新的 tracker，中断前的用量清零重算。对不中断的常规跑法（默认 `unrestricted`
+ `full_react`）预算是完整累计、正确拦截的；把预算记进 checkpointer 状态是明显
更大的改动（要给每个角色节点的返回 dict 加 usage 累加字段），本次不做。

### 出口标准

`AGENT_ROOM_MAX_TOKENS` 设极小值 → developer 工具环第二轮 LLM 调用前抛
`BudgetExceededError` → SSE 收到 `task_error` 帧且 `budget_exceeded: true`；
默认（不设环境变量）zero behavior change，478/489 现有测试不破；`/healthz` 报告
当前预算天花板；新测试 ≥6（tracker 记账/超限/NoOp、transport 集成、graph 级
真实超限中止、RunManager 终态帧）；真 LLM 联调一次复现超限中止。

---

## 6.13 §6.9-3 通用 Guardrail 层 — ✅ 已完成（2026-06-30）

> 落地：`agent_room/guardrail.py` 新模块（`Guardrail`/`GuardrailFinding`/
> `GuardrailTripwire`/`scan()`），`memory/curated.py` 的 `_THREAT_PATTERNS` 收编成
> 唯一权威源。4 个可达检查点：input（`api.py` 图跑之前，HTTP 400）、tool_call
> （`WriteTextTool`/`ShellTool` 新增可选 `guardrail:` 字段）、tool_response
> （`developer_react.py` 重入时扫最新一批 `ToolMessage`——覆盖 MCP 工具返回值，
> 本次最高杠杆的新检查点）、output（`delivery.py` 最终交付文本）。`block` 命中
> 复用 §6.12 的 `RunManager` 通用异常兜底转 `task_error(guardrail_blocked=true)`，
> `warn` 命中落 `TaskState.events`。`RoleBindings.guardrail` NoOp-default；
> `AGENT_ROOM_GUARDRAIL`(off/warn/block) 环境变量；`/healthz.guardrail` 报告
> mode。**故意的架构取舍**（写进模块 docstring + SECURITY.md，非事后补救）：
> tool_call 只做 write_text/shell 两个内置工具，不做 `awrap_tool_call` 级别的
> 全工具 wrapper（会跟 `tool_mode=approval` 占用同一个 LangGraph 扩展点冲突）；
> tool_response 反而覆盖更全（不分内置/MCP，统一扫 ToolMessage）。**真实服务器
> 联调**：`AGENT_ROOM_GUARDRAIL=block` 起服务器，`/healthz.guardrail` 正确报
> `mode:"block"`；真实 HTTP 请求验证 input 检查点——带越狱短语的
> `POST /tasks`/`POST /tasks/stream` 两次复现 `400 {"detail":"rejected by
> guardrail: injection pattern matched"}`，图从未启动；干净请求正常放行、任务
> 正常开跑。（tool_call/tool_response/output 三点已用 25 个新增测试通过真实
> `graph.ainvoke()` 精确验证，含一次真实 LLM 意外行为的诚实记录——见下方
> "调试笔记"。）**25 新测试**（核心模块 10 + tools tool_call 6 + developer_react
> tool_response 2 + graph output 2 + api input+healthz 5），**533 全绿、独立性
> 68、mypy strict 0 错**。
>
> **调试笔记（诚实记录一次真实 LLM 的意外行为，不是 bug）**：live 联调最初想用
> "读一个预先放好注入内容的文件"来验证 tool_response 检查点，但 per-task
> workspace 隔离（§6.10）意味着任务开始前不知道 task_id，没法预先把文件放对
> 目录。改成"读一个不存在的文件"后，DeepSeek-V4-Pro **没有报错**，而是创造性地
> 自己写了一个"演示用恶意代码库"（socket 信标、cron 后门等，作为安全审计教具）
> 外加一个安全替代版——完全合理但不是我预期的注入模式，guardrail 正确地没拦
> （这是 prompt injection 检测,不是通用"代码写得像恶意软件"检测,边界符合设计）。
> 第二次改成"把这句注入短语原样写进文件"，结果**input 检查点先一步拦下**了
> 请求本身（因为短语出现在任务描述里）——反而意外证明了纵深防御生效。鉴于
> tool_call/tool_response 已经被 25 个确定性离线测试精确钉死（含真实
> `graph.ainvoke()` 跑通整张图，不是 mock 捷径），没有再烧额外真 LLM 调用去凑一
> 个"干净"的 tool_response 现场演示；PLAN 承诺的出口标准原本就明确写的是"真 LLM
> 联调一次复现 **input** 点拦截"，这一条已经真实复现两次。

### 动机

原 PLAN 两处对"4 点"的描述不一致：早期 C3 条目写"input / tool-call /
tool-response / output"（按执行阶段分），§6.9.2 任务表写"输入 / 输出 / tripwire /
审计"（按功能分）。两者不是同一套 4 点，先在这里调和：**采用执行阶段版**（更符合
"guardrail 挂在哪个真实检查点上"这个工程问题），tripwire（越限即中止）和审计（结果
落 `state.events`）作为**贯穿 4 个检查点的共享机制**，不是独立的第 5/6 点。

### 设计要点（复用现有 infra + 可达性驱动选点，同 §6.12 方法论）

**新模块 `agent_room/guardrail.py`**：`Guardrail`（`mode: off|warn|block`，NoOp-default
同 `ContextEngine`/`MemoryProvider`/`Budget` 形状）+ `GuardrailFinding`（category/
pattern/checkpoint）+ `GuardrailTripwire`（`block` 模式命中时抛，携带 finding）+
`scan()` 纯函数。**模式集**：把 [`memory/curated.py`](agent_room/memory/curated.py)
的 `_THREAT_PATTERNS`（11 条注入/exfil 正则，已经过 v0.5 生产验证）**移进来做唯一权威
来源**，`curated.py` 改为从这里导入，不再维护第二份会漂移的清单；新增一小组 PII/
密钥形状正则（email、AWS access key、通用 `sk-`/`api-key` 形状）——**轻量模式匹配，
不是完整 PII/NER 检测**，故意不做（见下）。[`memory/scrubber.py`](agent_room/memory/scrubber.py)
不动：那是不同问题（流式 fence 抑制，不是威胁检测），不在"收编"范围内。

**4 个检查点**（逐一验证过可达性，不是拍脑袋加 hook）：

1. **input**——`server/api.py` 的 `create_task`/`stream_task`，图跑之前扫
   `TaskRequest.title+description`。`block` 模式直接拒（`create_task` 返回 400；
   `stream_task` 从不启动 run，直接发 `task_error`），是 4 点里代价最低的一点——
   连图都没起。
2. **tool_call**——`WriteTextTool`/`ShellTool` 新增可选 `guardrail:` 字段，`_run`
   执行前扫 `content`/`command`。**诚实的窄化**：只做这两个内置写/执行工具（复用
   `_safety.py` 已有的"只管这两个工具"先例），不做成 `awrap_tool_call` 级别的
   全工具通用 wrapper——那会跟 `tool_mode=approval` 已经占用的同一个 LangGraph
   扩展点冲突（`ToolNode` 的 `awrap_tool_call` 和默认执行路径互斥，§6.12 已验证过
   这个限制），组合两个 wrapper 超出本次范围。`block` 命中复用**既有**
   `handle_tool_errors=_tool_error_to_message` 机制反馈给 LLM（图不用改一行）；
   **已知局限**：`approval` 模式下 `_run` 内部抛的 `GuardrailTripwire` 不会走那层
   fallback（approval wrapper 自己合成拒绝消息，没有 catch-all），会跟其他任何
   `_run` 内部异常一样直接向上传——不是 guardrail 专属的新问题，如实记录不假装
   处理了。
3. **tool_response**——`developer_react.py` 重入时（`existing` 非空）读到的
   `state["dev_messages"]` 尾部就是上一轮工具执行产生的 `ToolMessage`，送进下一次
   LLM 调用前扫描——**这是本次最有实际价值的新增点**：防的是恶意/被攻陷的 MCP
   server 通过工具返回值做 prompt injection（上周才接上 MCP 生态，这个风险这周才
   真实存在）。覆盖面比 tool_call 点更全：不管是内置工具还是任意 MCP 工具的返回
   都会经过这条路径，不用逐工具适配。
4. **output**——`delivery.py` 的最终交付文本，`tx.invoke` 拿到内容后、`return`
   之前扫一遍——整条流水线里内容离开系统前的最后一道。

**Tripwire + 审计（贯穿机制，不是新状态）**：`block` 命中从节点内部抛异常，走
§6.12 已经验证过的同一条路（节点不捕获→graph 停→`RunManager` 通用异常兜底→
`task_error(guardrail_blocked=true, ...)`）——**零新服务层代码**。`warn` 命中不
中止，节点把 `Event(type="guardrail_triggered", ...)` 塞进自己返回 dict 的
`events` 列表——复用 `TaskState.events` 已有审计字段，不新增 state 字段。**故意的
不对称**（跟 Budget 一样）：`block` 命中因为异常直接掀桌，节点从未真正 `return`，
所以不会有 `TaskState.events` 条目，只有 SSE 层面的 `task_error` 通知；这跟
`BudgetExceededError` 现在的行为完全一致，不是本次引入的新缺口。

**配置**：`RoleBindings.guardrail: Guardrail`（NoOp-default）；
`Settings.guardrail_mode` + `AGENT_ROOM_GUARDRAIL`(off/warn/block) 环境变量；
`/healthz.guardrail` 报告当前 mode。

### 故意不做

- 完整 PII/NER 检测（姓名、地址等语义级识别）——正则模式匹配的准确率上限，加更多
  只会堆假阳性，不是这个量级的任务该做的。
- `tool_call` 点覆盖 MCP 工具的调用参数——需要 `awrap_tool_call` 级别的通用
  wrapper，跟 approval 模式冲突，架构改动超出本次范围（`tool_response` 点已经
  覆盖了 MCP 工具的返回内容，是更高杠杆的那一半）。
- 逐 checkpoint 独立 mode 配置（比如 input 用 block、output 用 warn）——单一
  全局 `mode` 更简单，没有真实需求证明要拆分（CLAUDE.md §6.1.4 反对无三个具体
  例子的抽象）。

### 出口标准

`AGENT_ROOM_GUARDRAIL=block` + 描述里带越狱短语 → `POST /tasks/stream` 400 或
`task_error(guardrail_blocked)`；4 个检查点各至少一个可复现的离线测试 + 1 个
graph 级集成测试；`warn` 模式命中出现在 `TaskState.events`；默认（`off`）
零行为变化，现有测试不破；`/healthz.guardrail` 报告 mode；curated.py 现有测试
在模式清单搬迁后依然全绿；新测试 ≥8；真 LLM 联调一次复现 input 点拦截。

---

## 6.14 §6.9-4 混合语义/向量记忆 — ✅ 已完成（2026-07-01）

> 落地：`TranscriptStore`（`agent_room/memory/fts.py`）加可选 `embedding:` 构造
> 参数；新增 `agent_room/memory/embedding.py`（`EmbeddingBackend` Protocol、
> `NoOpEmbeddingBackend` 默认、`HashingEmbeddingBackend`——依赖零增量的字符
> n-gram 特征哈希，不是深度/transformer embedding，诚实记在下方"设计要点"）+
> `agent_room/memory/vector.py`（`VectorStore` 包 sqlite-vec `vec0` 表，按
> `rowid` 对齐 transcript 消息 id）。`search()` 融合 lexical（既有 FTS5+LIKE）与
> vector KNN 两路，reciprocal rank fusion（常数 60）按消息 id 去重排序；
> `embedding=None`（默认）零改动，既有全部 FTS5 测试原样通过。`AGENT_ROOM_MEMORY_VECTOR`
> (默认 0) 开关；**消融数字**：4 组重排序 CJK 复述 query/fact 对，**FTS5-only
> 命中率 0/4，hybrid 命中率 4/4**。**真实服务器联调**：`/healthz` 报
> `memory_vector:true, memory_vector_backend:"hashing"`；真实 DeepSeek-V4-Pro
> 任务后直接查 `agent_room.db`，`agent_room_memory_vectors` 与
> `agent_room_memory_messages` 行数一致（7/7）。**21 新测试，554 全绿、独立性
> 70、mypy strict 0 错**。详见 §0 changelog 2026-07-01 行。

### 动机

`MemoryProvider` 的 transcript 层（v0.5 §5.3）只有 FTS5 + LIKE 兜底两条词法召回
路径；FTS5 默认分词器把 CJK 逐字拆开、LIKE 兜底要求整段 query 原样作为子串出现，
两者都拿"打乱语序的同义复述"没办法——这正是 PLAN §6.9.1 定的"补齐无向量召回硬
差距"缺口，也是北极星（消融表）明确要求的"可消融数字"类工作。

### 设计要点

**为什么是 sqlite-vec 而不是 chroma**：仓库已经把 `AsyncSqliteSaver`/`aiosqlite`
当作唯一持久化技术栈（CLAUDE.md §2「不引入额外数据库」），`sqlite-vec` 是纯
SQLite 扩展、复用同一个 `agent_room.db` 文件、同一套 `aiosqlite` 连接管理模式，
`chroma` 会引入一整套独立的向量数据库运行时，违反"复用难且通用部分"的原则
（CLAUDE.md §1.3）。

**为什么是字符哈希 embedding 而不是真 embedding API**：环境里没有配置任何
embeddings API key（`OPENAI_API_KEY` 未设，DeepSeek/Anthropic 网关都不提供
embedding 端点），引入 `sentence-transformers` 这类本地模型会带来数百 MB 的
ML 依赖，与"最少代码做到生产可用"（CLAUDE.md §1）冲突。`HashingEmbeddingBackend`
纯用 stdlib `hashlib`+`math` 做字符 trigram 特征哈希（+ 符号翻转 + L2 归一化），
零依赖、零网络调用、确定性可测试。**诚实的能力边界**：这不是深度语义 embedding,
抓不住"零字符重叠的真同义词"（比如"猫"和"喵星人"没有共享 trigram，这个哈希
方案帮不上忙）；它买到的是**字符级相似度**，对 CJK 尤其有意义——FTS5 分词器把
每个汉字当独立 token、丢失短语结构，而字符 trigram 哈希天然对连续汉字序列的
重排/局部重叠敏感（消融数字驗证的正是这一点：整句语序打乱，FTS5+LIKE 完全找
不到，字符哈希仍然靠共享片段把正确答案排到第一）。`EmbeddingBackend` 只有一个
方法的 Protocol,换成真 OpenAI `text-embedding-3-small`（或任何其他 API/本地
模型）是一次性的 drop-in 实现，**不做只是因为这次没有对应 API key**，不是架构
做不到。

**融合算法（reciprocal rank fusion，标准做法非自创）**：FTS5 的 `rank`（BM25）
和 sqlite-vec 的 cosine distance 完全不是同一个量纲，直接加权求和没有意义；RRF
只看排名不看原始分数（`1/(60+rank+1)`,60 是文献里的标准常数),两路各自取
`max(k, 3k)` 候选池再融合截断到 `k`,两路都命中的消息天然排得更靠前。命中两路
时优先保留 lexical 的高亮 snippet（`>>>...<<<`),vector-only 命中退化用
`content[:120]`。

**为什么 `Settings.embedding_backend()` 返回 `EmbeddingBackend | None` 而不是
永远返回一个实例**：开发过程中**先做错过一次**——最初实现让 `build_server_memory`
无条件传一个 embedding 实例（关时是 `NoOpEmbeddingBackend()`）给
`FileFtsMemoryProvider`,而 `TranscriptStore` 只判断 `self._embedding is not None`
就去挂 sqlite-vec 扩展、建 `vec0(embedding float[0])` 表——`NoOpEmbeddingBackend()`
虽然什么都不做但**是一个 truthy 的非 None 对象**,这会导致哪怕开关关着,向量层
也会被真实挂载（且 `dim=0` 大概率直接建表出错）,彻底违反"NoOp-default 零行为
变化"这条贯穿全项目的铁律。测试 `test_build_server_memory_no_vector_by_default`
断言 `provider._transcript._embedding is None` 时抓到了这个问题；修法是把
`Settings.embedding_backend()` 的返回类型改成 `EmbeddingBackend | None`,关时
真返回 `None`,而不是返回一个"什么都不做但存在"的实例。这个教训被记进代码
docstring,提醒以后任何新增的 NoOp-default 开关都要检查同样的坑。

**aiosqlite 扩展加载**：`aiosqlite.Connection` 没有公开的
`enable_load_extension`/`load_extension` API（这两个是 `sqlite3.Connection`
上的方法）,`VectorStore.attach()` 落到 `conn._execute`/`conn._conn` 在 aiosqlite
的后台线程上直接跑这两个调用——这是 aiosqlite 自己的 issue tracker 对"如何加载
扩展"给出的推荐写法,不是绕过封装,mypy 对应加了一条 `no-untyped-call` 豁免（同
LangGraph/LangChain 边界豁免的性质,是 stub 严格度问题不是产品代码 bug）。

### 故意不做

- 真 transformer/API embedding（OpenAI `text-embedding-3-small` 等）——需要
  额外依赖 + 已配置的 API key,这次环境不满足,且 Protocol 已经为它留好了
  drop-in 位置,不是不能做,是没必要现在做。
- CJK 专用分词器（jieba/lindera）——v0.5 §5.3 就已经把这个记成 v0.6+ 的
  scope cut,本次没有新理由提前做。
- 向量层写权限（LLM 自己决定要不要 embed 某条内容）——`sync_turn` 对每条
  transcript 消息无条件 embed,跟 curated 层"LLM 主动写"的语义不同,没有做成
  可选的理由。
- embedding 缓存 / 批量 embed 优化——`HashingEmbeddingBackend` 是纯 CPU 哈希,
  单条内容 embed 是微秒级操作,没有测出需要优化的场景。

### 出口标准

`AGENT_ROOM_MEMORY_VECTOR=0`（默认）零行为变化,既有 533 个测试原样通过；
`AGENT_ROOM_MEMORY_VECTOR=1` 时 `/healthz.tool_envelope` 报告
`memory_vector:true, memory_vector_backend:"hashing"`；`TranscriptStore`
hybrid 融合正确性（RRF 排序、lexical-only 一致行为、vector 命中）+ NoOp 默认
零行为变化 + 消融数字（FTS5-only vs hybrid 命中率）全部有离线测试覆盖，新测试
≥8（实际 21 个）；真实服务器 + 真 LLM 联调至少一次验证向量索引确实写入
真实运行产生的数据（不只是离线测试通过）。

---

## 6.15 Qdrant 可插拔向量后端 + 物理级租户隔离 — ✅ 已完成（2026-07-01）

### 需求演进（三步迭代，值得记录为什么最终方案长这样）

1. 起点：把 §6.14 的零依赖向量记忆做成**可插拔后端**，加 Qdrant 档，选一个合适
   的 embedding 模型。
2. 用户追加：记忆子系统也要**租户隔离**。最初设计是"共享 SQLite/Qdrant + `tenant_id`
   列/payload 做逻辑过滤"——sqlite-vec 那档只能"过采样再丢弃"做近似过滤。
3. 用户再追加：**LangGraph checkpointer 的租户隔离也要一起做**。查代码发现
   `open_checkpointer(bindings, db_path)` 对整个服务器进程只开一次,`root_dir`
   完全由 `db_path` 推导——**给每个租户一个独立 `db_path` 这一件事,同时解决了
   checkpointer/curated/transcript/sqlite-vec 四层隔离**，不需要在任何一层加
   `tenant_id` 列/过滤逻辑。第 3 步的物理隔离方案**整体替代**（不是叠加）第 2
   步的逻辑过滤方案——更彻底、代码量反而更少。

### 设计要点

**核心机制**：`tenant_id="default"`（默认）→ `settings.db_path` 原样不变，零
迁移；其它 `tenant_id` → `{db_path 所在目录}/tenants/{tenant_id}/agent_room.db`
（全新文件，`open_checkpointer` 现有逻辑自动把 curated MEMORY.md/USER.md、
transcript FTS5/sqlite-vec 都带过去——`curated.py`/`fts.py`/`memory/provider.py`
**一行都没改**）。新组件 [`agent_room/server/tenancy.py`](agent_room/server/tenancy.py)
的 `TenantCheckpointerPool` 用 `AsyncExitStack` 管理"运行时才知道有多少个、
什么时候开"的一组 `open_checkpointer`，懒加载 + 加锁防重复开，服务器关闭时
一起 `aclose`。`clone_bindings_for_tenant()` 克隆 `RoleBindings`，`budget`/
`guardrail`/角色模型绑定安全共享（无状态 config），只有 `memory` 每租户一份
新实例（`FileFtsMemoryProvider` 内部持有可变连接，不能跨租户共享）——`tenant_id
="default"` 直接复用调用方原始的 `.memory`，不构造第二个丢弃的实例，保证
100% 向后兼容（包括测试注入的 fake provider）。

**`SessionStore` 轻量逻辑隔离 + API 归属校验**：`agent_room_sessions` 加
`tenant_id` 列（元数据量小、敏感度低，不需要物理分表），`get_task`/
`resume_task`/`resume_stream`/`cancel_task` 都加 `tenant_id` query 参数，
校验请求方声明的 tenant_id 与已知 task 的记录一致，不一致 404（跟"任务不
存在"同一返回码，不额外泄露"这个 task_id 属于别的租户"这个信息本身）。**诚实
边界**：这个校验只跟"调用方是否诚实"一样可靠——agent-room 不做身份认证，
挡不住存心冒充别的租户的调用方；它挡的是应用层 bug/UI 状态陈旧导致的**意外**
跨租户访问,是纵深防御,不是认证（SECURITY.md 原话记录）。

**Qdrant 插件化**：新 `VectorIndex` Protocol（`agent_room/memory/vector_index.py`）
只描述"外部显式传入"的那种后端，不强行让 sqlite-vec 的 `VectorStore` 也实现
它——`VectorStore` 天生要共享 `TranscriptStore` 的 aiosqlite 连接，Qdrant 天生
不需要，两者签名对不上是真实的不对称,伪造统一签名违反 CLAUDE.md §6.1.4。
`TranscriptStore.__init__` 加 `vector_index: VectorIndex | None = None`，`None`
时走今天内部自建 `VectorStore` 的路——21 个既有测试零改动。`QdrantVectorIndex`
（embedded `path=` 默认，`url=` 可选切远程/云端，符合"pip install 零外部服务"
不变式）+ `FastEmbedBackend`（默认模型 `jinaai/jina-embeddings-v2-base-zh`——
**验证过而非猜测**：`fastembed==0.8.0` 的 `TextEmbedding.list_supported_models()`
里根本没有 `BAAI/bge-m3`，这个常被引用的"最佳多语言 embedding"在这个 fastembed
版本里不存在；`jina-embeddings-v2-base-zh` 是经查证真实可用、且官方文档明确
写"支持中英混排输入"的最接近选择，正好匹配这个项目真实的中英混排内容——不是
事后凑合的降级）。CPU 推理成本真实（`prefetch()` 同步挡热路径），`asyncio.to_thread`
包住 `TranscriptStore.append()`/`search()` 里的 embed 调用（两种后端统一路径），
防止 `RunManager` 共享事件循环上的其它并发任务被这次推理拖慢——不是让当前
任务变快，是不连累别的任务。

### 消融数字（两组，展示"真语义 embedding 该赢在哪"）

复用 §6.14 的语序打乱复述固定集（4 组，字符有重叠只是顺序打乱）：
**FTS5-only 0/4、sqlite-vec+hashing 4/4、Qdrant+fastembed 4/4**——三档在这组
打平，因为哈希方案本来就吃字符重叠，这组数据两种向量方案都能靠共享 n-gram/
真语义命中。

新增一组**零字符重叠真同义词**（"喵星人"/"猫"、"汪星人"/"狗狗"等,fact 和
query 之间几乎没有共同汉字）：**FTS5-only 0/4、sqlite-vec+hashing 3/4**（个别
条目靠巧合的字符片段重叠命中,不是真语义）、**Qdrant+fastembed 4/4**——这才是
真正验证"哈希方案抓不住的真语义相似度,transformer 方案抓得住"这句话的数字，
不是空口叙事。

### 打包 / 测试

`[project.optional-dependencies] vector = ["qdrant-client>=1.9", "fastembed>=0.3"]`，
懒加载 + `ImportError` 提示，照抄 `tools/mcp.py` 先例；`agent_room/memory/__init__.py`
不 eager-import `QdrantVectorIndex`/`FastEmbedBackend`（没装 `[vector]` extra
的用户 `import agent_room.memory` 不该报错）。离线测试（`tests/test_memory_vector_qdrant.py`
9 个 + `tests/test_tenancy.py` 7 个 + `tests/test_api_tenancy.py` 4 个，共 20
个）全部零网络（`QdrantClient(path=tmp_path)` 是纯本地文件），`pytest.importorskip("qdrant_client")`
挡在文件顶部让没装 extra 的环境优雅跳过而非报错。新建 `tests/integration/`
放真模型下载测试（`test_fastembed_backend.py`，验证真实语义相似度数字）+ 真
Qdrant server 冒烟（`test_qdrant_live_smoke.py`，env-gated，无 server 时跳过）——
**必须堵上的真实漏洞**：`pyproject.toml` 的 `testpaths=["tests"]` 会把新目录
纳入默认收集除非显式排除，加了 `norecursedirs = ["tests/integration"]`。**双向
验收**：装了 `[vector]` extra 后新测试全绿；**不装** extra 的干净 venv 里
`pytest -q`/`mypy agent_room/`/`ruff check` 全部仍然全绿（实测两遍，见下）。

### 中途踩坑记录（诚实写，不是顺利到底）

**mypy + numpy 2.x 兼容性**：装了 `[vector]` extra 后 `mypy agent_room/` 报
`numpy/__init__.pyi:737: error: Type statement is only supported in Python
3.12 and greater`——`sqlite_vec`/`fastembed`/`qdrant_client` 都会transitively
拉到 numpy 2.x,其自带 `.pyi` 用了 PEP 695 `type X = ...` 语法,mypy 只在
`python_version >= 3.12` 才解析这种语法,**跟 `follow_imports=skip`/
`ignore_missing_imports` 都无关**（这些设置只影响"要不要跟进模块内容",
不影响"能不能解析已经存在的 `.pyi` 桩文件本身"，试了给 `sqlite_vec`/
`fastembed`/`qdrant_client`/`numpy` 逐个加 `follow_imports=skip` 都没堵住,
最终确认是 mypy 版本语法解析层面的限制,不是配置能绕开的)。**修法**：
`[tool.mypy] python_version` 从 `3.11` 提到 `3.12`——这只影响 mypy 自己的
stub 解析,不影响仓库真实的 `requires-python = ">=3.11"` 运行时约束（写了
清楚的行内注释区分两者,避免以后有人误以为项目运行时要求也提到 3.12了)。
其次是 `agent_room/memory/{qdrant_index,fastembed_backend}.py` 里第三方
客户端对象都标 `Any` 而不是导入真实类型（`AsyncQdrantClient`/`TextEmbedding`
的方法签名本身也会引用 numpy 类型,即使标注不到用不着 numpy 的精度,mypy
仍需要完整解析类型来源)，加上 `sqlite_vec`/`fastembed`/`qdrant_client` 的
`follow_imports=skip` 覆盖（这个仍然值得保留,减少不必要的第三方内部检查）。

### 故意不做

- ❌ 认证/授权层——`tenant_id` 的可信来源不是这次范围（见上信任边界）。
- ❌ 租户连接的空闲驱逐/关闭——开了就活到服务器关闭,没有 LRU 淘汰,这个项目
  的作品集/演示规模没有实测出需要。
- ❌ `SessionStore` 物理分库——元数据量小、敏感度低,逻辑列+归属校验够用。
- ❌ `vector_backend`/租户路由做成 per-task 请求级覆盖——跟 budget/guardrail
  一样是部署级配置。
- ❌ sqlite-vec 档做到跟 Qdrant 一样的租户隔离——已经被"物理隔离"方案整体
  替代,不再需要（第 2 步方案的遗留问题在第 3 步里自然消失）。

### 出口标准

`AGENT_ROOM_VECTOR_BACKEND=sqlite`（默认）+ `tenant_id` 不传 = 现有单租户
行为字节级不变；`vector_backend=qdrant` 时真实模型/collection 维度不匹配
fail-loud；两个不同 `tenant_id` 各跑一个任务后磁盘上确实是两个独立的
`agent_room.db`/`memory/` 目录；跨租户请求已知 `task_id` 返回 404；新测试
≥8（实际 20 个离线 + 4 个 integration）；真实服务器 + 真 LLM 联调验证两个
租户数据物理隔离；三档消融数字（含零字符重叠真同义词那组）写入本节。

---

## 6.16 目标模式（goal mode）——项目第二运行模式（2026-07-02 立项并落地）

### 定位:双模式

- **workflow 模式**（`full_react`,默认不变):固定流水线跑一遍,修订预算耗尽即停。
- **goal 模式**（`AGENT_ROOM_GRAPH=goal`,新):给定目标持续循环
  developer→verify,直到**客观 oracle**（任务方在 `TaskRequest.verify_command`
  提供的命令,exit 0 = 达成)通过才退出;没给命令时退回 reviewer 主观
  `approved`。轮数上限（`max_iterations`,默认 10)降级为防失控兜底,触发即
  failed——"预算耗尽"从常规退出方式变成异常。

### 拓扑决策:静态拓扑 + 动态轨迹 + 混合路由（经两轮讨论定案）

机械决策全部确定性（`goal_router`:验证时机/通过退出/失败回炉/上限刹车)——
LLM 路由这些没有悬念的决策只会引入"路由抽风"式不可归因失败（实验副本
ADR-0014 的教训)。LLM 判断力精确投放在唯一真需要判断的点:**连续失败
`STUCK_EVERY`(3) 轮后**,`goal_router` 确定性地绕道一次 `supervisor` 节点,
用 `StuckDecision` 结构化输出四选一:continue（继续修)/replan（回 planner
重新规划,新计划带失败上下文)/ask_user（**复用 need_user_decision 契约**,
现有暂停/恢复机制零改动可用)/abort（承认不可达成,failed)。

### 核心机制:transcript-push（"谁产出新信息,谁推进 transcript"）

**顺带修掉一个主线真 bug**:ReAct developer 的 `dev_messages` 只增不清、
初始 prompt(含 review_feedback 层)只在首次进入时渲染——reviewer 打回后
developer 拿着旧 transcript **盲目重试,反馈永远看不见**（`full_react` 的
修订循环一直是坏的,纯文本 developer 不受影响)。修法确立统一模式:任何节点
要让在途 developer 看到新信息,就在返回值里带
`{"dev_messages": [HumanMessage(...)], "dev_round": 0}` 借 `add` reducer 追加
(尾部追加,prefix-cache 友好;重置工具预算)。三个节点用这一个模式:
**reviewer**（feedback+issues,bug fix)、**verifier**（失败输出+修复指引,
goal 循环的迭代方向来源)、**planner**（`# Revised plan`,replan 路径)。
`dev_messages` 为空时不追加——所有既有 preset 零行为变化。

### verifier 节点（`roles/verifier.py`,非 LLM 纯框架代码）

workspace 取 registry 里 `read_text` 工具的 `root`（与 fs 工具同一 per-task
围栏);`verify_files` 每轮验证前经 `resolve_within_root` 校验后**重新写入**
（防篡改:developer 改测试文件让测试通过这条真实作弊路径被机制性堵住,
e2e 有专门用例);命令过与 developer shell 工具**同一个**
`AGENT_ROOM_SHELL_ALLOWLIST` 白名单+元字符拒绝（不扩大执行面);
`create_subprocess_exec` + 60s 超时（照 eval/coding.py 的 oracle 范本);
白名单拒绝/起不来/超时都变成 `VerificationResult(passed=False,
exit_code=None)` 可见失败,不炸整个 run。

### 落地清单

新增:`roles/verifier.py`、`roles/supervisor.py`、`presets/goal.yaml`、
`routers.py::goal_router/stuck_router/STUCK_EVERY`、schema
`VerificationResult`/`StuckDecision`/`TaskRequest.verify_command/verify_files/
max_iterations`、`TaskState` 6 新字段、`RoleBindings.supervisor`。
修改:reviewer/planner（transcript-push)、`_materialize_status`
（abort→failed、验证上限→failed,**顺带对齐 max_revisions 缺省值 0/2 预存在
不一致**)、serde allowlist、`Role`/`RoleName` 加 verifier/supervisor。

**41 个新测试**（verifier 12 + 路由 13 + transcript-push 7 + goal e2e 9,
e2e 全部驱动真编译图+真 subprocess oracle,覆盖:失败反馈进 transcript 后
二轮通过/上限 failed/卡住→supervisor 四个动作各自路径/replan 注入新计划/
无命令退 reviewer/篡改 check 文件被 reseed 击败),**615 全绿、mypy strict
0 错(76 文件)、独立性 76 子模块、ruff clean**。

### 故意不做

- ❌ 全 supervisor 驱动路由——与"客观信号驱动收敛"卖点冲突（对比过三个选项
  后用户选定混合形态)。
- ❌ 验证输出语义解析（失败测试名结构化提取)——尾部原文喂回,LLM 自己读。
- ❌ per-iteration checkpointer 分支/回滚——每轮在同一 workspace 上迭代。
- ❌ `STUCK_EVERY` 请求级配置——常量起步。
- ❌ 修 `materialize_result` 的"接受裸 dict"死代码路径（`hasattr(dict,
  "values")` 恒真,else 分支不可达)——预存在问题,与本次无关,记录待办。

### 出口标准

✅ 全部达成:goal preset 编译出设计拓扑;41 新测试全绿且既有 574 个零破坏;
verify_command 过 shell 白名单;篡改 check 文件被 reseed 击败有 e2e 钉死;
ask_user 走现有 awaiting_user/resume 机制;`AGENT_ROOM_GRAPH` 不设 goal 时
零行为变化。

**真 LLM 冒烟（DeepSeek-V4-Pro,2026-07-02,两个任务)**:
- **任务 1（一次通过路径)**:罗马数字加法约定转换,附 10 例 check.py。DeepSeek
  读懂"加法约定"陷阱**第一次就写对**,round-1 验证 exit=0 → delivery,
  status=completed。验证了 happy path + oracle 真跑 subprocess。
- **任务 2（迭代路径,核心价值验证)**:median(nums),描述只说"standard
  behavior"(标准中位数=平均值),但 check.py 偶数长度要求**下中位数**,失败
  输出附一句提示。真实轨迹:`planner → developer → verify(r1 FAIL exit=1)
  → developer → verify(r2 PASS exit=0) → delivery`,status=completed。
  **决定性证据**:最终 stats.py 的 docstring 明确写"For an even-length list,
  returns the lower of the two middle elements"——这条规则**只出现在 round-1
  的 check.py 失败输出里**,任务描述从未提及。开发者第二轮采纳它,坐实
  **失败反馈真的进入了下一轮 prompt**(即修掉的那个主线 bug + goal 迭代的
  核心价值,用真 LLM 端到端复现,不只是离线测试)。

### 前端双模式切换（2026-07-02,像素前端）

goal 模式一开始只在后端,前端点不出来。补齐:

- **按请求选图**(后端 ~15 行):`TaskRequest.graph` + `build_server_spec(graph_preset=)`
  + `task_service_for` 按 preset 名缓存现编图。白名单 `{full_react, goal}`——
  不开放任意 preset(防客户端选 `solo` 跳过 review)。服务器默认仍是
  `full_react`,goal 是**每请求**opt-in,不再需要 `AGENT_ROOM_GRAPH=goal` 重启。
- **SSE 事件补齐**:`events.py` 的 formatter 只认四个角色节点,goal 的
  `verify`/`supervisor` 节点产出原本不进 SSE 流(只在最终快照里)。加了两个
  分支把 `verification_completed`/`supervisor_decided` 从节点 output 直接发出,
  UI 时间线才能**实时**看到迭代过程。
- **像素前端**(全加法):composer 加 workflow/goal 分段切换,goal 时展开
  验证命令 + 验证文件(可增删行)+ 最大轮数表单;`streamTask` 按模式发
  `{graph, verify_command, verify_files, max_iterations}`;两个新事件走
  `sse.ts`→`taskStore`→时间线渲染(目标验证第 N 轮通过/未通过、策略决策);
  header 加模式角标 + 最新验证结果 chip。
- **测试**:后端 +8(build_server_spec override / api 白名单 400 / formatter
  两事件),前端 vitest +9(normalize 两事件 + store 两分支 + submissionBody
  workflow/goal body)。**623 后端全绿 + 20 前端全绿、mypy/ruff/独立性/tsc/
  build 全过**。
- **真 LLM 端到端**:服务器默认 `full_react` 起,一个请求带 `graph=goal` +
  median 任务(描述含糊、check 要下中位数)→ 真实轨迹
  `目标验证 第1轮 未通过(exit=1) → 目标验证 第2轮 通过 → 交付`,status
  completed;SSE 流里实测抓到两条 `verification_completed` 帧(payload
  `{passed, exit_code, round}` 正是前端 normalize 读的形状)。同服务器不带
  `graph` 的普通请求仍走 workflow、`verification` 为 None,零回归。

### 双模式对照实验（2026-07-03,modes 套件）

回答"goal 模式相比 workflow 值多少"——coding 套件 13/13 满分,量不出增量,
需要 workflow 会**系统性失败**的任务集。

- **设计**:6 个欠规格陷阱任务(`evals/mode_tasks.py`,描述只说 "Standard
  behavior",验收强制合法但反直觉的约定:下中位数 / .5 远离零 / 丢尾块 /
  保留末次出现 / 下划线 slug / 跳过无效 token)× 2 变体(`evals/variants.py`
  `mode_workflow`=full_react / `mode_goal`=goal preset)。公平性:同一
  TaskRequest(verify 字段在 workflow 图上惰性)、预算对齐(max_revisions=6
  = max_iterations=6,唯一变量是主观审查 vs 客观验证)、check.py 与隐藏
  pytest 同一份 cases 生成(oracle 零漂移)、评分只跑 `test_hidden.py`
  (防开发者自写测试污染)。runner 加 `extra.verify_round` 遥测(一行,
  非 goal 图恒 0)。
- **离线冒烟**:6 陷阱双向验证(约定实现→check exit 0 + 隐藏套件过;
  自然实现→exit 1 且输出带 NOTE)+ 两图对任务 registry 编译 + 623 全绿。
- **结果**(DeepSeek-V4-Pro,12 次运行 0 错误,`snapshots/eval/modes/`):
  **workflow 1/6(17%)vs goal 6/6(100%)**;goal 侧 5/6 任务
  `verify_round=2`(r1 自然实现挂 → 失败尾部 transcript-push → r2 修正过),
  机制证据直接;唯一 workflow 过的任务(round_half_away)恰是 goal 侧唯一
  r1 就过的——陷阱强度自洽。成本:goal 多 66% token(104,872 vs 63,275),
  平均墙钟持平(73.9s vs 74.2s)。
- **结论**:在"规格含糊但验收严格"的任务形态上,客观验证循环把通过率从
  17% 拉到 100%,代价是 token;这是 goal 模式存在的量化理由。

---

## 7. 跨版本任务（持续）

### 7.1 文档

- [x] 写 `docs/adr/0001-langgraph-as-orchestrator.md`（解释为什么不用 CrewAI / AutoGen）
- [x] 写 `docs/adr/0002-reviewer-protocol.md`（三个 decision 值的合同）
- [x] 写 `docs/adr/0007-borrow-not-integrate-hermes.md`（独立性原则的 ADR）
- [x] 写 `docs/architecture.md`（一张图 + 数据流）

### 7.2 工具链

- [x] 加 ruff + pre-commit（见 `.pre-commit-config.yaml` + `pyproject.toml [tool.ruff.lint]`）
- [x] 加 mypy 严格模式（`pyproject.toml [tool.mypy]` strict=true；agent_room/ 0 errors，46 sources。tests/examples/scripts/benchmarks 暂排除，留待 P3-D 之后）
- [x] GitHub Actions：lint / test（`.github/workflows/ci.yml`，3.11/3.12/3.13 矩阵 + 独立性 job）
- [x] **独立性断言**：pre-commit local hook 跑 `! grep -RnE "from hermes_|import hermes_" agent_room/ tests/ examples/`；CI 同时检查 `pyproject.toml` 不含 hermes-agent 依赖
- [x] coverage 报告（`pyproject.toml [tool.coverage]` `fail_under=90` / pytest-cov + coverage 进 dev 依赖；CI 在 3.13 跑 `--cov` 三种 reporter，`coverage.xml` + `htmlcov/` 上传成 14 天 artifact；README badge + Coverage 章节。基线 336 测试 / 1918 stmts / **93%**）

### 7.3 examples/

每个里程碑必须新增至少一个示例：
- [x] `examples/streaming.py`（v0.1 补）
- [x] `examples/resume_after_user_decision.py`（v0.1 补，离线可跑）
- [ ] `examples/yaml_pipeline.py`（v0.2）
- [x] `examples/dev_runs_tests.py`（v0.3 — DeepSeek-V4-Pro 实测一次过 fix → pytest exit 0）
- [x] `examples/context_compression.py`（v0.4 §4.8 — 30 轮合成 ReAct 跑三引擎可视化压缩 plateau；v0.4.x §3.4 spill 接入后加 `_spill_demo()` 实测 3.0x 缩减）
- [x] `examples/memory_recall.py`（v0.5 — session 1 写、session 2 读，4 项跨会话检查全 True）

### 7.4 测试缺口

- [x] FastAPI 端点测试（用 httpx.AsyncClient + asgi-lifespan）
- [x] CLI 测试（typer.testing.CliRunner）
- [x] 错误路径：LLM 返回非法 JSON 时 reviewer 节点行为——`retry_on_parser_error`
  落地时补的（P3-A / ADR-0013），见 `tests/test_llm_retry.py` + `tests/test_llm_transport.py`；
  这条勾选之前一直漏更新，本次盘点顺手补上
- [ ] **并发**：同一 task_id 并发 run 的行为——`RunManager.start()`/`.get()`
  没有专门测过"两个请求同时抢同一个 task_id"这种竞态；§6.15 的
  `TenantCheckpointerPool.get()` 测过类似的双开竞态（`test_get_is_cached_not_reopened`），
  但那是"同一租户的池子入口"不是"同一 task_id 的 run"，是相邻但不同的问题，
  仍然是空的
- [x] **独立性测试**：`tests/_independence_driver.py` + CI `independence` job
  已覆盖（P3-E，2026-06-12 done），这条也是漏更新的旧勾选

---

## 8. 立即下一步（v0.3 收官后）

按优先级排序：

| 优先级 | 任务 | 估时 | 输出 | 状态 |
|-------|------|------|------|------|
| ~~P0~~ | v0.3 §3.1 内置 4 工具（shell / read / write / glob） | 2d | `agent_room/tools/{fs,shell,_safety}.py` + 35 测试 | ✅ done |
| ~~P0~~ | v0.3 §3.2 ToolRegistry（借鉴 hermes 模式，本仓库自研） | 1d | `agent_room/tools/registry.py` + `register_builtin_tools()` | ✅ done |
| ~~P1~~ | v0.3 §3.3 工具白名单 + 三种权限模式（read-only / approval / unrestricted） | 1d | `agent_room/tools/policy.py` + `NodeSpec.tool_mode` + 16 测试（approval 占位待实现） | ✅ done |
| ~~P1~~ | v0.3 §3.5 角色子图：agent ↔ tools（developer 起步） | 2d | `agent_room/roles/developer_react.py` + ToolNode 接入 + 13 测试 | ✅ done |
| ~~P2~~ | v0.3 §3.6 + 3.7 测试：mock 工具 / 多轮调用 / 安全 | 2d | `tests/test_tools.py`、`tests/test_tool_security.py` | ✅ 实质完成（35 测试见 §3.1 落盘时） |
| ~~P2~~ | v0.3 §3.9 example：developer 真跑 pytest | 1d | `examples/dev_runs_tests.py`（实测沙盒：DeepSeek 一次过 fix → pytest exit 0）+ 8 离线测试 | ✅ done |
| ~~P2~~ | v0.3 §3.8 文档：工具开发指南 | 1d | `docs/tools.md`（quick start + 4 内置工具 + 3 权限模式 + 自定义工具 + ReAct 预算 + 安全包络），同步 graph-spec.md tools 段 | ✅ done |
| **P2-A** | v0.3.x §3.3 续：approval 模式落地（LangGraph `interrupt()` + 检查点恢复 + `service.resume(task_id, tool_call_decision)`） | 1d | `agent_room/tools/_approval.py` + `awrap_tool_call` 接入 ToolNode + `service.resume(at_node="tool_call")` + `TaskResult.pending_tool_calls` + 22 测试 | ✅ done |
| **P2-B** | v0.3 §3.4 大输出 spill 落盘（合并到 v0.4 RISK-9 / ADR-0009） | 1d | 推迟 v0.4 与 artifact 统一存储一起设计 | ✅ done 2026-06-13 |
| **P3-A** | provider 偶发 `OutputParserException: Unknown tool type: ''` 容错（DeepSeek/Ark） | 0.5d | `agent_room/llm/_retry.py`（新）+ `retry_on_parser_error` 包装 reviewer / planner_gate / reviewer_two_call 共 4 处 `with_structured_output` + 10 测试 | ✅ done |
| **P3-B** | v0.2 收尾烟雾发现归并：把 F2-F5 carry-forward 项标记进 v0.3/v0.4 任务卡 | 0.5h | 本文件 §8 + findings 文档交叉引用 | ✅ done |
| **P3-C** | mypy 严格模式（§7.2） | 1d | `pyproject.toml [tool.mypy]` + CI 矩阵 + 报告基线 | ✅ done |
| **P3-D** | coverage 报告（§7.2） | 0.5d | `pyproject.toml [tool.coverage]` + CI artifact + README badge | ✅ done |
| **P3-E** | 独立性硬指标测试（§7.4 / §12.4） | 0.5d | `tests/test_independence.py` + `tests/_independence_driver.py`：子进程装 `MetaPathFinder` 拦截 `hermes_*`，`pkgutil.walk_packages` 强制导入 `agent_room` 全部子模块；CI `independence` job 新增具名步骤跑这一条 | ✅ done |
| **§4.1** | v0.4 起步：`ContextEngine` 接口 + NoOp 默认 | 1d | [`agent_room/context/engine.py`](agent_room/context/engine.py) ABC（`should_compress` + `compress` + `apply` 便利方法）+ `NoOpContextEngine` + 11 测试 | ✅ done |
| **§4.2** | v0.4 `WindowedContextEngine`（保护窗口 + 截断） | 1d | 同上文件 + `WindowedContextEngine(max_messages, protect_first_n, protect_last_n)` + 19 测试（含构造硬不变量校验 + ReAct 边界对齐） | ✅ done |
| **§4.3** | v0.4 `SummaryContextEngine`（用小模型摘要中段） | 2d | [`agent_room/context/summary.py`](agent_room/context/summary.py) `SummaryContextEngine(WindowedContextEngine)`：父类抽出 `_partition` + 异步 `_build_marker` hook，子类只 override marker 生成（serialize → summarizer.ainvoke → HumanMessage with SUMMARY_HEADER/FOOTER）；summarizer 失败 / 空响应 / 空 middle 都 fallback 到父类 count marker + warning log + 12 测试 | ✅ done |
| **§4.4** | v0.4 分层 prompt 装配器 | 2d | [`agent_room/prompt/{builder,developer}.py`](agent_room/prompt/) `Layer + PromptBuilder + developer_prompt_builder()` 5 层（identity / plan_and_task / user_directives / review_feedback / extra_context），同 role 合并、None/empty skip、`extend()` 不可变追加；`developer.py` + `developer_react.py` 替换手写拼接，等价性 byte-level 钉住 + 23 新测试 | ✅ done |
| **§4.5** | v0.4 节点接入：developer ReAct 调 LLM 前先过 engine | 1d | [`RoleBindings.context_engine`](agent_room/config.py) 默认 NoOp + [`developer_react.py`](agent_room/roles/developer_react.py) `await engine.apply(convo)` 只压 prompt 不压 state + 4 集成测试 | ✅ done |
| **§4.6** | v0.4 测试：长对话触发压缩 + 保护窗口生效 | 1d | [`tests/test_context_long_task.py`](tests/test_context_long_task.py) 50 轮 ReAct 端到端 4 测试（Windowed 预算上限 / Summary 调用次数精确 / 保护窗口 byte-equal / state 审计完整） + 复用 `_ScriptedSummarizer` from §4.3 | ✅ done |
| **§4.7** | v0.4 文档：`docs/context.md` 设计说明 | 0.5d | [`docs/context.md`](docs/context.md) 215 行：为什么存在 / ABC 契约 / 三个引擎逐个解释 / 选哪个的 rule of thumb / 集成点 / 测试不变量索引 | ✅ done |
| **§4.8** | v0.4 长任务 PoC | 0.5d | [`examples/context_compression.py`](examples/context_compression.py) 30 轮合成 ReAct 跑三引擎，逐轮打印 state vs llm_input 表，可视化压缩 plateau；offline，无需 creds | ✅ done |
| **§5.1** | v0.5 `MemoryProvider` Protocol + `NoOpMemoryProvider` + `RoleBindings.memory` 字段 | 1d | [`agent_room/memory/provider.py`](agent_room/memory/provider.py) `MemoryProvider(Protocol)` 五方法（`initialize/system_prompt_block/prefetch/sync_turn/close`）+ `Memory` schema + `NoOpMemoryProvider` 默认绑定，v0.1-v0.4 行为 bit-for-bit 不变 + 8 测试 | ✅ done |
| **§5.2** | v0.5 `CuratedFileStore`（MEMORY.md + USER.md，frozen snapshot + 威胁扫描 + 字符上限 + 原子写） | 2d | [`agent_room/memory/curated.py`](agent_room/memory/curated.py) `\n§\n` 分隔条目 / `add+replace+remove` 三操作 / 11-pattern 威胁扫描 reject 给 LLM 当 tool result / 2200 char + 1375 char 双层 cap / `_atomic_write` rename / `system_prompt_block()` 读盘一次冻结整 session 保 prefix-cache + 25 测试 | ✅ done |
| **§5.3** | v0.5 `TranscriptStore`（SQLite + FTS5 + 三触发器 + LIKE/CJK fallback + OR 重写） | 3d | [`agent_room/memory/fts.py`](agent_room/memory/fts.py) 外部内容 FTS5 虚拟表跟 LangGraph checkpointer 共库无冲突 / `'delete'` pseudo-command 三触发器同步 INSERT/DELETE/UPDATE / FTS5 MATCH + `snippet()` 主路 + LIKE `substr(instr-40, 120)` CJK fallback / `_build_fts_query` OR 重写 + 50 词 stopword 表（demo 实测发现：默认 AND 把 `"now refactor the OAuth2 token handling"` 在 OAuth2-only 历史里搜出零结果）+ 22 测试 | ✅ done |
| **§5.3.1** | v0.5 `FileFtsMemoryProvider` 拼成默认后端 | 0.5d | [`agent_room/memory/file_fts.py`](agent_room/memory/file_fts.py) 把 curated + transcript 两层粘合到一个 `MemoryProvider` 实现，向 `MemoryTool` 暴露 `add_curated/replace_curated/remove_curated/live_curated_text` 写路径（不污染 base Protocol） | ✅ done |
| **§5.4** | v0.5 `MemoryTool`（action × target dispatch + opt-in 注册） | 1d | [`agent_room/memory/tool.py`](agent_room/memory/tool.py) 单一 BaseTool 两轴（`action: add\|replace\|remove`, `target: memory\|user`），`recall` 故意不暴露（auto-prefetch 更稳）；[`agent_room/tools/__init__.py`](agent_room/tools/__init__.py) `register_builtin_tools(reg, ..., memory_provider=None)` opt-in 默认关 + 11 测试 | ✅ done |
| **§5.5** | v0.5 developer_react 接入：第一次 entry 注入 + fire-and-forget sync_turn | 1d | [`agent_room/roles/developer_react.py`](agent_room/roles/developer_react.py) `_inject_memory(convo, memory, query=state["description"])` 把 curated 块 + `<memory-context>` 围栏 prefetch 块追加到 SystemMessage 后；`asyncio.create_task` fire-and-forget `sync_turn("user"/"assistant", ...)` 不阻塞 LLM 路径；ReAct re-entry（existing 非空）跳过注入保 prefix-cache + 7 测试 | ✅ done |
| **§5.6** | v0.5 跨会话集成测试 + docs + ADR + demo | 1d | [`docs/adr/0010-memory-architecture.md`](docs/adr/0010-memory-architecture.md) 六大决策 + 替代方案否决理由 / [`docs/memory.md`](docs/memory.md) 190 行用户视角 / [`examples/memory_recall.py`](examples/memory_recall.py) 209 行真跑端到端：session 1 写 → 关 provider → session 2 全新 provider 同 root_dir/db_path → 4 项 recall 检查全 True | ✅ done |
| **§6.1.1** | v1.0 后端：sessions 薄壳（schema + DAO + 4 路由） | 1d | `agent_room/server/sessions.py` 三列 `(id, name, created_at)` 表挂在与 checkpointer 同一 db_path 但**独立连接**；新增 `agent_room/server/compat_v1.py` 挂 `/api/agent-room` 前缀，提供 `GET/POST /sessions`、`GET /sessions/{id}`、`DELETE /sessions/{id}` + `GET /sessions/{id}/tasks` 关联表 + `POST /sessions/{id}/tasks` 透传 `service.run`；主路径 `/tasks` `/tasks/stream` `/tasks/{id}` 不动；测试 ≥ 12 个 | ✅ done |
| **§6.1.2** | v1.0 ADR-0011：12→4 状态映射 + session 语义 | 0.5d | `docs/adr/0011-vue-ui-state-mapping.md` 把 hermes-web-ui 12 状态显式映射到本仓库 4 状态 + `Event[]` 派生（`submitted_for_review` → `running` + 最近 event=node_start:reviewer，`review_passed`/`review_rejected` → 看 `ReviewerDecision`，`delivering` → `running` + node=delivery，`need_user_decision` → `awaiting_user`）；钉死 session = task 标签语义 | ✅ done |
| **§6.1.3** | v1.0 `/healthz` 扩展：暴露 role→model 映射 | 0.25d | `agent_room/server/api.py::healthz` 返回 `{"status": "ok", "role_bindings": {"planner": "...", "developer": "...", "reviewer": "...", "delivery": "..."}}`；从 `RoleBindings` 取，不暴露 API key；新增 1 测试 | ✅ done |
| ~~**§6.1.4**~~ | ~~v1.0 前端 SSE composable~~ | ~~1d~~ | ~~hermes-web-ui 内 Vue composable~~ | 🚫 superseded by ADR-0012 (2026-06-23) |
| ~~**§6.1.5**~~ | ~~v1.0 前端 store 改 SSE 驱动~~ | ~~1.5d~~ | ~~重写 hermes-web-ui store~~ | 🚫 superseded by ADR-0012 (2026-06-23) |
| ~~**§6.1.6**~~ | ~~v1.0 12→4 状态显示组件改造 + 砍掉不迁移视图~~ | ~~1d~~ | ~~hermes-web-ui Vue 组件改造~~ | 🚫 superseded by ADR-0012 (2026-06-23) |
| ~~**§6.1.7**~~ | ~~v1.0 联调 + e2e smoke + 文档~~ | ~~1d~~ | ~~跨仓联调~~ | 🚫 superseded by ADR-0012 (2026-06-23) |
| **§6.1.4'** | v1.0 仓内薄 UI：模板 + JS 客户端 | 1.5d | `agent_room/server/ui/templates/{base,index,task}.html`（Jinja2）+ `static/app.js` 单文件 SSE 订阅 + 12→4 状态派生（ADR-0011 表）+ resume 表单 + new-task 表单；零 npm，零构建 | ✅ done |
| **§6.1.5'** | v1.0 FastAPI UI 路由 + GET 流变体 | 0.5d | `api.py` 挂 `Jinja2Templates` + `StaticFiles` + `GET /ui` + `GET /ui/tasks/{id}` + `GET /tasks/{id}/stream`（POST 流保留）+ UI 路由测试（HTML 响应 + 200/404） | ✅ done |
| **§6.1.6'** | v1.0 smoke 文档 + 截图 + reconciliation 收尾 | 0.5d | `examples/ui_smoke.md`（启动 → 创建 → SSE → resume 全流程）+ 1 张 screenshot；`docs/v1.0-vue-ui-reconciliation.md` 末尾加"2026-06-23 ADR-0012 转向仓内薄 UI"小节；README 加 `/ui` 入口 | ✅ done |
| **§5.7** | v0.5.x `StreamingContextScrubber`（hermes 借鉴）：跨 chunk 围栏防泄漏 | 0.5d | `agent_room/memory/scrubber.py` 状态机 + `agent_room/events.py::StreamEventFormatter` 已接入真实 token stream；FastAPI `POST /tasks/stream` 与 CLI `--stream` 均使用 per-task scrubber；单测覆盖 split fences across chunks / 完整 fence in one chunk / partial non-fence flush / unclosed fence drop；理由：v0.5 prefetch 用 fence 包历史片段（[memory/prompt.py](agent_room/memory/prompt.py)），模型若 echo fence 或被 prompt-injection 诱导回放时，SSE 消费者会拿到内部 context | ✅ done 2026-06-23 |
| **§6.2.1** | v1.0 LLM transport 抽象（hermes 借鉴 `agent/transports/base.py`） | 1d | [`agent_room/llm/transport.py`](agent_room/llm/transport.py) `Transport(Protocol)` 三方法（`invoke` / `structured` / 流走 graph 不入 transport v1.0）+ `LangChainTransport` 默认实现 + `NormalizedResponse(message, usage, provider_data)`；7 role factories 加 `transport: Transport \| None = None` 默认 + 业务调用走 `tx.invoke/structured`；3 处手工 `retry_on_parser_error(with_structured_output)` 收敛到 `LangChainTransport.structured` 一处；CLAUDE.md §1 非目标松绑 + §2 禁止行更新 + §4.5 路由更新；规模上限 ≤ 3 文件 / ≤ 300 行 / ≤ 2 实现，超出需另立项 | ✅ done 2026-06-23 |

| **EVAL-1** | v1.0 重定向：任务集 + eval harness（**泛化 escalation_lab,非从零**） | 3d | **复用** `examples/escalation_lab/run.py` 矩阵驱动 + `RunRow` 遥测 + summary 表格 → 提取到 `agent_room/eval/{runner,report}.py`；新增 `Task.verify(result)->bool` 正确性 oracle（编码任务复用 `examples/dev_runs_tests.py` 沙盒+pytest）；`Variant` 从"只带 spec"升级到"spec + RoleBindings 工厂"（才能切 context/memory/transport）；成本经 `NormalizedResponse.usage` 采集 token；`evals/` ≥12 真任务 | ✅ done 2026-06-23（1a harness + 1b oracle + 13 CodingTask；full-suite clean 真跑 **13/13 (100%)** 默认 provider 零覆盖，`snapshots/eval/coding/summary.json`）|
| **EVAL-2** | v1.0 重定向：消融矩阵（北极星本体） | 2d | 在 EVAL-1 harness 上定义 5 轴消融变体（planner_gate / two-call / context NoOp vs Summary / memory NoOp vs FileFts / transport），跑真 LLM 出"成功率+成本 Δ"对照表 | ✅ done 2026-06-23（4 个有意义轴真跑：planner_gate 25→100% / context Windowed-FAIL→Summary-token / memory FileFts-PASS vs NoOp-FAIL / two-call 三向 25/75/100%；transport 非真消融仅一实现。N=1 噪声未降到 N≥3——纯成本，留作可选）|
| **WRITE-1** | v1.0 重定向：作品集技术叙事 | 1.5d | [`docs/portfolio.md`](docs/portfolio.md)：planner_gate 25%→100% + F1 find-and-fix + 3 反直觉判断 + omission + limitations，每个挂真实证据 | ✅ done 2026-06-23 |
| **HYG-1** | v1.0 重定向：§0 流水账压成一页 changelog | 0.5d | §0 由 61 行密集流水账 → 30 行一页 changelog（一行一里程碑）；详细条目原样移 [docs/archive/ledger-v0.x.md](docs/archive/ledger-v0.x.md)；6 处悬空 §0 引用改指归档 | ✅ done 2026-06-23 |

**推荐次序**（v0.3 收官 → v0.4 起步的过渡周）：

1. ~~**P3-A（OutputParserException 容错，0.5d）** —— 真实数据驱动（v0.2 §2.9 实测 DeepSeek 1/9 翻车），改动独立单文件，立即让 v0.3 工具栈对真实 provider 更稳。~~ ✅ done 2026-06-12
2. ~~**P2-A（approval 模式真实落地，1d）** —— v0.3.x 闭环最后一块，三个 `tool_mode` 都真实可用，v0.3 真正完整。~~ ✅ done 2026-06-12（170/170）
3. ~~**P3-E（独立性硬测，0.5d）** —— ADR-0007 立身之本，把"靠 grep"升级到"靠运行测试"，进入 v0.4 之前先把地基坐实。~~ ✅ done 2026-06-12（171/171）
4. ~~**v0.4 §4.1 ContextEngine 接口起步（1d）** —— 进入下一里程碑。~~ ✅ done 2026-06-12（182/182；ABC + NoOp 默认；windowed/summary 留 §4.2/§4.3）
5. ~~**v0.4 §4.2 `WindowedContextEngine`（保护窗口 + 截断，1d）** —— 第一个真实可用的引擎，落地保护策略后才好接 §4.5。~~ ✅ done 2026-06-12（198/198；消息条数预算 + 头/尾保护 + 中段 marker；构造硬不变量保证幂等）
6. ~~**v0.4 §4.5 节点接入：developer ReAct 调 LLM 前先过 engine（1d）** —— 先把 §4.2 真接进 ReAct 循环，把"可用"变成"在用"；§4.3 SummaryEngine 和 §4.4 prompt builder 紧随其后。~~ ✅ done 2026-06-12（205/205；engine 压 prompt 不压 state；NoOp 默认行为零变化；ReAct 边界对齐确保压缩后 tool_call/tool_result 配对仍合法）
7. ~~**v0.4 §4.3 `SummaryContextEngine`（用小模型摘要中段，2d）** —— 从"丢弃带 marker"升级到"摘要 + 占位"。需要 spill 接入（v0.3 已落盘）保留关键工具结果。~~ ✅ done 2026-06-12（217/217；subclass `WindowedContextEngine`，只 override marker 生成；summarizer 失败 / 空响应 / 空 middle 三路退化到父类 count marker；spill 接入仍 deferred 到 RISK-9 / ADR-0009）
8. ~~**v0.4 §4.4 分层 prompt 装配器（2d）** —— `agent_room/prompt/builder.py`，借鉴 hermes prompt_builder 但精简到 4-5 层。和 §4.3 解耦，可与之并行。~~ ✅ done 2026-06-12（240/240；`Layer + PromptBuilder` 5 层，`developer.py` + `developer_react.py` 替换手写拼接，byte-level 等价性钉住）
9. ~~**v0.4 §4.6 测试：长对话触发压缩 + 保护窗口生效（2d）** —— 50 轮 ReAct 端到端真跑通是 v0.4 的出口标准。先离线 mock LLM 跑通，再考虑接 DeepSeek 实测。~~ ✅ done 2026-06-12（244/244；4 个离线测试钉住 50 轮 Windowed/Summary 双引擎、保护窗口 byte-equal、state 审计完整；真 LLM 联调留到 §4.7/§4.8 之后）
10. ~~**v0.4 §4.7 + §4.8 文档 + 长任务 PoC（2d）** —— `docs/context.md` 设计说明 + `examples/context_compression.py` 让用户看到真长任务自动收敛。~~ ✅ done 2026-06-12（244/244；docs/context.md 215 行；examples/context_compression.py 离线 PoC 跑三引擎可视化 plateau；真 LLM 联调推到 v0.5）
11. ~~**§3.4 spill 接入 SummaryEngine（1d）** —— 大 ToolMessage 走 spill 占位，摘要器只看摘要友好的输入。RISK-9 / ADR-0009 一起设计。~~ ✅ done 2026-06-13（263/263；`agent_room/tools/spill.py` `SpillStore` ABC + `InMemorySpillStore` content-hashed 幂等 + `maybe_spill_tool_message` / `spill_messages`；`SummaryContextEngine` 加可选 `spill_store` + `spill_threshold`，默认 `None` 完全不变；摘要 prompt 由 raw payload 限定 → 由占位符长度限定，30 KB stdout 摘要从 ~30K char 降到 ~600 char；19 个新测试钉住 store 内容寻址 / 占位符幂等 / 引擎集成 / 不破坏 marker 形状；3-layer turn-aggregate budget / `read_text` 协调 / disk 后端推到 v0.5）
12. ~~**v0.5 §5.1-§5.6 跨会话记忆全部落地（一周内一次性闭环）** —— 两层（curated `MEMORY.md` + `USER.md` / transcript SQLite+FTS5）+ 一工具（`memory(action,target)` 不暴露 recall）+ 冻结快照 prefix-cache 友好；`developer_react` 第一次 entry 注入 + fire-and-forget `sync_turn`；FTS5 默认 AND→OR 重写 + stopword 过滤是 demo 实测发现的 load-bearing 修复。~~ ✅ done 2026-06-14（336/336；73 新测试 + 6 新模块 + 2 文档 + 1 ADR + 1 端到端 demo；独立性 39 → 46 子模块；出口标准全部通过——session 1 写、session 2 fresh provider 读到，零 hermes import）

**v1.0 接力（推荐次序）**：

1. ~~**生产化短板**（P3-C / P3-D）—— mypy 严格模式 + coverage 报告。改动小、价值大；进 v1.0 前把工程地基补齐。~~ ✅ done 2026-06-14（P3-C：`agent_room/` 46 文件 strict 0 错；P3-D：`fail_under=90` 基线 336/336 测试 1918 stmts **93%**，CI 上传 `coverage.xml` + `htmlcov/` 14 天 artifact，README badge + Coverage 章节）
2. ~~**仓内薄 UI**（§6.1.4'-§6.1.6'）—— 2026-06-23 撤回 hermes-web-ui 复用方案（ADR-0012）。改用 FastAPI + Jinja2 + 单文件 `app.js`（~300-500 行 server-rendered，零 npm 零构建），挂在 `/ui`。~~ ✅ done 2026-06-23（373/373，独立性 48→50 子模块；详见 [ledger](docs/archive/ledger-v0.x.md) ledger 末条）
3. ~~**API 冻结 + SemVer** / **Postgres** / **Prometheus** / **OTEL** / **PyPI**~~
   🚫 **2026-06-23 砍出 v1.0 关键路径**（见"项目目标·北极星"）。这些是生产化基础设施，
   不服务"用数据证明设计判断"的北极星，移到 v2.x 可选项。

**v1.0 重定向后的关键路径（EVAL → WRITEUP → 收口）**：

3. **EVAL-1 任务集 + harness（2d）** —— `evals/` 下 ≥ 12 个真实任务（FizzBuzz 级 →
   小 bug-fix 级，公开可复现），`agent_room.eval` runner 跑一个 graph config 出
   pass/fail + cost/latency 表。LLM 响应可缓存以便确定性复跑。
4. **EVAL-2 消融矩阵（2d）** —— 北极星本体。逐个开关 planner_gate / two-call review /
   context engine（NoOp vs Summary）/ memory（NoOp vs FileFts）/ transport，
   产出对照表：每个设计选择对成功率 + 成本的 Δ。
5. **WRITEUP（1.5d）** —— `docs/portfolio.md`：3-4 个反直觉判断，每个挂 EVAL-2 的数字。
   这是面试时直接甩出去的东西。
6. **§0 压缩 + 收口（0.5d）** —— §0 流水账 → 一页 changelog，详细条目移
   `docs/archive/ledger-v0.x.md`；README 顶部加 eval 表。

剩余可选（不阻塞作品投递）：§5.7 scrubber wiring、生产化栈（v2.x）。

**v0.1 烟雾测试历史发现**（2026-06-11，详见 [`docs/findings/2026-06-11-smoke-v0.1.md`](docs/findings/2026-06-11-smoke-v0.1.md)）：

P3-B 对账完成（2026-06-14）—— F1-F5 五项 carry-forward 全部追溯到落地点：

- **F1** `extract_text` helper 把 `thinking` block 拆出 → v0.1 已修，6 测试见 [`tests/test_extract_text.py`](tests/test_extract_text.py)
- **F2** reviewer 不返回 `need_user_decision` → v0.2 §2.9 escalation lab 落地，DeepSeek 实测 `planner_gate` 2/3 (67%) / `two_call_review` 1/1 排除 parser-error 100%，详见 [ledger](docs/archive/ledger-v0.x.md) v0.2 ledger 第 4-5 行
- **F3** RISK-9 artifact spill → v0.4 §3.4 落地 `agent_room/tools/spill.py` + `SummaryContextEngine.spill_store` 接入，详见 [ledger](docs/archive/ledger-v0.x.md) v0.4 ledger
- **F4** latency 数据驱动 per-role 模型选择 → 能力既存（`RoleBindings` 4 字段 + `AGENT_ROOM_{ROLE}_MODEL` env），未触发"if it bites"条件，留作运营层杠杆
- **F5** `load_dotenv(override=False)` 静默忽略 .env → 烟雾脚本自己 `override=True` + 打印 provenance 规避；library 端保持 caller env 优先语义；v0.4.x DeepSeek 联调中 `ANTHROPIC_BASE_URL` 子路径 gotcha 已落 §0；library `load_settings(diagnostics=True)` 留 v1.0

---

## 9. 决策日志（精简版 ADR）

> 完整 ADR 在 `docs/adr/`。本节是索引 + TL;DR。

| ID | 决定 | 状态 | 一句话理由 |
|----|------|------|-----------|
| ADR-0001 | 用 LangGraph 而不是 CrewAI / AutoGen | accepted | 状态机控制 + checkpointer 内建 + structured output 最适合本场景 |
| ADR-0002 | Reviewer 用 `with_structured_output` 而不是 JSON 解析 | accepted | 强 schema，省掉容错代码 |
| ADR-0003 | SQLite 用 LangGraph 自带 checkpointer，不自己建表 | accepted | 不重复造轮子，跨进程恢复直接拿到 || ADR-0004 | 节点之间禁止共享状态，全部走 TaskState | accepted | 单一状态源是 LangGraph 的核心契约 |
| ADR-0005 | ~~不在本项目内做 RAG / 长期记忆~~ → **改为：在本项目内做，借鉴 hermes 模式自研** | superseded | 原计划是依赖 hermes-agent，已废弃；现行方案见 ADR-0007 |
| ADR-0006 | Vue 前端不重写，复用现有客户端 | proposed | 节省 1-2 周工作量；前提是 API 形状可对齐 |
| **ADR-0007** | **借鉴 hermes-agent 架构但不依赖它**（独立性原则） | **accepted** | 用户明确要求："分析 hermes 形成文档的原因就是借用它的架构"。删除 hermes-agent 目录后本项目必须仍能正常工作 |
| **ADR-0010** | **memory 用两层 + 一工具 + 冻结快照**（v0.5） | **accepted** | curated（LLM 主张）+ transcript（事后搜索）一个 schema 装不下；`recall` 不暴露因为 auto-prefetch 更稳；冻结快照保 prefix-cache，跨 session 才隔代生效 |
| **ADR-0011** | **Vue UI ↔ server 状态映射 + sessions 薄壳**（v1.0 §6.1） | **accepted** | sessions 只装 `(id, name, created_at)` 三列做 task 标签；12 状态 UI 标签由 4 状态 + `Event[]` + `ReviewerDecision` 派生（一向映射，前端 if/else）；SSE 主路径 polling 砍掉；role binding 留 v1.0 dataclass+env，UI 通过扩展 `/healthz` 只读 |
| **ADR-0012** | **仓内薄 UI 替代 hermes-web-ui 复用**（v1.0 §6.1） | **accepted** | hermes-web-ui 复用 ~4.5d 工作量已逼近重做 + 使用面留下"必装外部前端"的独立性漏洞；改用 FastAPI + Jinja2 + 单文件 app.js（~300-500 行 server-rendered，零 npm 零构建）挂 `/ui`；ADR-0011 状态映射表不变，由仓内 UI 沿用；`/api/agent-room/*` 兼容面保留给自建 SPA；预算降到 ~2.5d |
| **ADR-0013** | **轻量 LLM transport 抽象**（v1.0 §6.2.1） | **accepted** | 撤回 v0.x 旧"非目标 不做 LLM provider 抽象"规则；4 处 `retry_on_parser_error(with_structured_output)` 重复 + v1.0 prompt-cache/reasoning_effort/Gemini thought_signature 进度需要统一插入点 + reviewer 协议本就是 transport-shaped 契约 + hermes `agent/transports/base.py` 89 行 shape 已在 5+ provider 验证；引入 `Transport` Protocol + `LangChainTransport` + `NormalizedResponse`，规模上限 ≤ 3 文件 / ≤ 300 行 / ≤ 2 实现；rejected：保留 v0.x 规则（重复噪音持续）/ 借 hermes 全栈 transport（~3700 行违反"用最少的代码"）/ 纯装饰器（已是 retry_on_parser_error 的就这种 pattern）|

---

## 10. 风险登记 (Risk Register)

| ID | 风险 | 概率 | 影响 | 缓解 |
|----|------|------|------|------|
| RISK-1 | LangGraph 大版本 breaking change | 中 | 高 | pin 版本范围，定期跟踪 changelog |
| RISK-2 | OpenAI / Anthropic structured output 行为差异 | 中 | 中 | 统一通过 LangChain 抽象，针对性测试 |
| RISK-3 | SQLite 在高并发下锁争抢 | 中 | 中 | v1.0 提供 Postgres 选项 |
| RISK-4 | 节点超时 / LLM 卡死 | 中 | 高 | v0.2 加 LangGraph node-level timeout + retry policy |
| RISK-5 | Tool 调用安全（命令注入 / 路径越权） | 中 | 高 | v0.3 默认 approval 模式 + allowlist |
| RISK-6 | SSE 帧过多冲爆前端 | 低 | 中 | v0.2 加节流 + chunk 聚合 |
| RISK-7 | 抄 hermes 的代码而不是借鉴模式（污染独立性） | 中 | 高 | code review 时显式检查；CI 加 grep 断言；ADR-0007 兜底 |
| RISK-8 | 上下文压缩 / 记忆功能比 hermes 退化 | 中 | 中 | v0.4/v0.5 设计前先写"hermes 行为对照清单"；测试覆盖关键场景 |
| RISK-9 | `TaskState` reducer 无封顶（`artifacts` / `user_directives` 用 `add`） → checkpoint 体积线性增长 | 低（v0.1-v0.3）/ 中（v0.4+） | 中 | v0.1 已给 `events` 加 `capped_append(500)` 兜底；2026-06-11 烟雾测试实测 50 轮极端任务约 280KB state，SQLite checkpointer 无压力，**spill 设计推迟到 v0.4**；v0.2-v0.3 不阻塞，届时跟工具大输出落盘合并设计（写 ADR-0009「artifact + tool_result 统一存储后端」），用真实 tool 输出量级数据驱动方案选型 |

---

## 11. 工作流约定

### 11.1 怎么"领"任务

1. 在本文件第 8 节挑一项 P0/P1。
2. 创建 `feature/<scope>` 分支。
3. 改完跑 `pytest -q`，全绿才提 PR。
4. PR 描述里勾选 [CLAUDE.md §7.3](CLAUDE.md) 模板的复选框。

### 11.2 怎么"立"新任务

1. 先在本文件对应版本下加任务行。
2. 估时 + 列依赖。
3. 必要时写 ADR。
4. 在 PR 里同时改本文件 + 代码。

### 11.3 怎么"砍"任务

任务在 PLAN 里超过 4 周没动 → 移到 [docs/archive/](docs/archive/)，标注"deferred / 原因"。
不要让本文件膨胀成"不会做的事的合集"。

---

## 12. 度量指标 (Definition of Success)

每个版本完成时，下面三类指标都要记录在 [docs/releases/](docs/releases/) 下。

### 12.1 代码量

- LoC（不含测试）：v0.1 当前 ~600，v1.0 目标 < 2500（含 v0.3-v0.5 自研模块）
- 测试 LoC / 业务 LoC 比：目标 ≥ 0.5
- pytest 时长：目标 < 5s

### 12.2 性能（真实 LLM）

- 简单任务（FizzBuzz）端到端时延：目标 < 30s
- 复杂任务（带 1 次修订）：目标 < 90s
- 流式首 token 延迟：目标 < 3s

### 12.3 用户体验

- "30 行 Python 跑通"：v0.1 已达成
- "CLI 一行命令上手"：v0.1 已达成
- "可视化运行轨迹"：v1.0 目标（Vue UI）

### 12.4 独立性（硬指标）

- ✅ `agent_room/` 全文不出现 `from hermes_` / `import hermes_`（CI `independence` job 源码 grep + pre-commit local hook）
- ✅ `pyproject.toml` 任何 dependency / optional-dependency 不引用 hermes-agent（CI 同 job 第二步 grep）
- ✅ 运行时硬测：[`tests/test_independence.py`](tests/test_independence.py) 在子进程里挂 `MetaPathFinder` 拦截 `hermes_*`，再 `pkgutil.walk_packages` 全量强制导入 `agent_room`。这条等价于 PLAN 起初约定的 `rm -rf /home/ly/hermes-agent && pytest -q`，但更便携（无需操作宿主目录），同时也能抓到 grep 看不见的懒导入 / `importlib.import_module(...)` 计算名 / `__getattr__` 触发的间接引用。CI `independence` job 已加具名步骤。

---

最后修改：2026-06-12
下次评审：v0.2 启动前
