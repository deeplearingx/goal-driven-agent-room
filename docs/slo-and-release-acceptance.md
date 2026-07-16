# SLO 与发布验收

本文件定义目标和验收证据，不把目标当成已经达成的事实。每次正式发布都必须填充
对应的记录，未提供证据即视为未通过。

## 初始 SLO

| 服务面 | SLI | 初始目标 | 误差预算 / 处置 |
| --- | --- | --- | --- |
| Gateway 准入可用性 | `POST /api/v1/tasks` 的非 5xx 比例，排除计划维护 | 月度 ≥ 99.9% | 43.2 分钟/月；预算耗尽时冻结非紧急发布 |
| Gateway 准入延迟 | `agent_room_admission_duration_seconds` | 5 分钟窗口 p95 ≤ 250ms、p99 ≤ 500ms | 连续 15 分钟超标则告警并检查 DB/Outbox/限流 |
| 命令持久化 | 已接受任务对应的 `tasks` 与 Outbox 记录 | 0 丢失；幂等重试只返回同一 task ID | 任意不一致为 P1，停止扩容/发布并保留证据 |
| Outbox 恢复 | `agent_room_outbox_oldest_seconds` | 正常运行 < 30s；> 120s 触发页级告警 | Broker 恢复后必须回到基线；否则按 Runbook 处置 |
| 异步投递 | RabbitMQ 命令/事件 DLQ 深度 | 0 持续消息 | 非零即告警；不得手工重放原 message ID |

任务本体的模型时延、成功率和成本受 Provider 与任务类型影响，必须单独按
`runtime_invocations` 与 Eval Run 报表观察，不与 Gateway 准入 SLO 混合。

## 指标与 PromQL

Gateway 导出无高基数标签的准入 histogram：
`agent_room_admission_duration_seconds`。按如下表达式计算分位数：

```promql
histogram_quantile(0.95,
  sum(rate(agent_room_admission_duration_seconds_bucket[5m])) by (le))

histogram_quantile(0.99,
  sum(rate(agent_room_admission_duration_seconds_bucket[5m])) by (le))
```

可用性需要在入口或反向代理记录带状态码的请求指标；Gateway 自身的
`agent_room_gateway_requests_total` 与 `agent_room_gateway_rejected_total` 只能用于
容量/拒绝趋势，不能替代 HTTP 成功率 SLI。这样不会将 401、429 等预期控制面
结果错误当作服务不可用。

## 发布准入清单

发布负责人必须把以下字段写入变更单或 `artifacts/release-<date>.md`：

```text
Release / change ID:
Gateway / Worker image digests:
Migration version (latest schema_migrations row):
CI run URL + all image scan / SBOM artifacts:
Admission k6 summary JSON:
Idempotency k6 summary JSON:
RabbitMQ recovery drill time range + metric exports:
PostgreSQL restore drill artifact + verifier output:
Current 5m p95 / p99 admission latency:
Current Outbox age / command DLQ / event DLQ:
Eval Run ID and release-gate outcome:
Approver (platform):
Approver (application owner):
Rollback image digests and decision owner:
```

任一项缺失、SLO 未达标、存在未处置的高/严重镜像漏洞、恢复演练未通过，均不得
标记为 production accepted。紧急变更可以临时豁免，但必须记录批准人、时限和
补做验收的日期。

## 发布后观察与回滚

1. 金丝雀阶段只放行少量 Gateway 副本和受控租户，观察至少 15 分钟。
2. 若 p99 超过 500ms、Outbox 年龄持续上升、DLQ 非零或错误预算异常消耗，立即停止
   扩大流量。
3. 回滚应用 image digest，不回滚数据库 schema；将事件、k6/Prometheus 导出和审计
   记录关联到变更单。
4. 仅在指标恢复、队列已清空且负责人签字后恢复常规发布。
