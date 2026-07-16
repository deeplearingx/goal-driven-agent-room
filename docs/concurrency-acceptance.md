# P7 并发与故障恢复验收

这是一份上线前的**证据清单**，不是性能承诺。所有压测只能在隔离的
非生产租户执行，且压测期间应停止会消费模型的 Worker，避免合成任务产生
Provider 成本。

## 通过条件

| 场景 | 命令/观测 | 通过条件 | 必须留存的证据 |
| --- | --- | --- | --- |
| 准入吞吐 | `k6 run gateway/load/k6.js` | HTTP error rate < 1%，p95 < 250ms；目标速率由部署容量决定 | k6 `summary.json`、请求速率、Gateway/DB/RabbitMQ 规格 |
| 幂等重试 | `k6 run gateway/load/k6-idempotency.js` | 两个 POST 都是 202；`idempotency_task_id_mismatch=0` | k6 `summary.json`、抽样 task ID、`tasks`/`outbox` 行数 |
| 租户公平性 | `TENANT_COUNT=20` 运行上述两个脚本 | 所有被测租户均有接受请求，未出现非预期 429/503 | 各租户请求计数、限流配置 |
| RabbitMQ 中断 | 停止 Broker 60 秒，再恢复 | 准入仍由 PostgreSQL Outbox 持久化；恢复后 Outbox 年龄回落、消息不丢失 | 中断前后 `/metrics`、队列深度、Outbox 行统计 |
| Gateway 滚动重启 | 负载运行中逐个重启副本 | 无重复 task/outbox 消息；短暂连接中断后客户端可重连 | k6 结果、部署事件、task/outbox 去重 SQL |
| Soak | 以目标速率运行至少 60 分钟 | 无持续 p99 漂移、内存爬升、连接池耗尽或 DLQ 增长 | 每 5 分钟指标快照、资源曲线、DLQ 截图/导出 |

## 命令

将 `BASE_URL` 指向隔离环境的 Gateway；当环境使用服务 API key 时传入
`API_KEY`。不要在 shell 历史或提交记录中保存真实凭据。

```bash
# 基础准入：默认 500 req/s，仅作为目标而非结论。
k6 run gateway/load/k6.js \
  -e BASE_URL=https://gateway.staging.example \
  -e RATE=100 -e DURATION=5m -e TASK_PREFIX=admission-YYYYMMDD \
  --summary-export artifacts/p7-admission-summary.json

# 每个 VU 的每轮都用同一 Idempotency-Key 连续提交两次。
k6 run gateway/load/k6-idempotency.js \
  -e BASE_URL=https://gateway.staging.example \
  -e VUS=20 -e TENANT_COUNT=20 -e DURATION=5m -e TASK_PREFIX=idem-YYYYMMDD \
  --summary-export artifacts/p7-idempotency-summary.json
```

`k6-idempotency.js` 支持 `ITERATION_SLEEP_SECONDS`。默认不节流，适合
寻找容量上限；持续验收应显式设置该值，避免固定 VU 场景变成无限速请求源。

若 Gateway 采用 OIDC，使用专为压测创建且权限最小的 service principal；默认脚本
**不会发送租户头**，让 Gateway 从该身份派生租户。不要为了 k6 打开
`GATEWAY_TRUST_TENANT_HEADER`。多租户场景使用每个租户的受限 service token 分别运行，
或仅在受信代理的集成环境明确传入 `TENANT_HEADER_NAME=X-Tenant-ID`；绝不能让公网
客户端自行提供这个头。

## 故障恢复步骤

1. 记录起始时的 `agent_room_outbox_pending`、
   `agent_room_outbox_oldest_seconds`、命令/事件 DLQ 深度和 PostgreSQL
   `outbox` 未发布行数。
2. 保持 admission 脚本运行，隔离 RabbitMQ 60 秒；不要删除 Outbox，也不要手工
   重发消息。
3. 恢复 RabbitMQ，等待 Outbox publisher 通过持久化指数退避自行恢复。
4. 在 Outbox 清空或回到基线后，核对每个被测 `Idempotency-Key` 只对应一个
   `task_id`，每个 `message_id` 只对应一条 Outbox 消息；再检查 DLQ 为零。
5. 将起止时间、版本 digest、迁移版本、k6 JSON、指标导出和异常日志一同存入
   发布记录。任一阈值失败都不能把本次结果标为通过。

## 为什么这些检查能覆盖实现

- Gateway 对每个租户保存有界 limiter，超出容量显式返回 503，而不是无限增长
  内存；单租户超速返回 429 与 `Retry-After`。
- PostgreSQL 在同一事务里写入 task、审计与 Outbox；`(tenant_id,
  idempotency_key)` 的唯一约束让重试复用已创建的 task。
- Outbox 通过 `FOR UPDATE SKIP LOCKED` 认领，发布失败时记录错误与下一次尝试时间，
  成功后才写入 `published_at`。单元测试覆盖这两个状态转换；集群故障注入验证跨
  进程恢复的真实行为。

真实容量结果取决于 PostgreSQL IOPS、RabbitMQ 节点/磁盘、Gateway 副本、请求
大小、Worker 并发和模型配额；不得将任何默认脚本参数当作已达成的吞吐声明。
