# 2026-07-15 本地隔离验收记录

环境：Docker Desktop 4.64.0；独立 Compose 项目 `agent-room-acceptance`。
本次未启动任何模型 Worker，所有请求只落入隔离 PostgreSQL、Outbox 与
RabbitMQ。

| 项目 | 结果 | 证据 |
| --- | --- | --- |
| 生产镜像构建 | 通过 | Gateway、Migrate、Python Worker、Eino、Knowledge、Webhook 共六个镜像构建成功 |
| 数据库迁移与网关 | 通过 | PostgreSQL、RabbitMQ、Redis 健康；`/healthz` 返回 `{"status":"ok"}` |
| 备份恢复 | 通过 | `scripts/restore-drill.ps1` 恢复到隔离 pgvector 容器；`tasks`、`outbox`、vector 扩展和 13 条迁移均通过 |
| 幂等并发 | 通过 | k6：5 VU、15 秒、500ms 节流；300 请求零失败，150 次重试均返回同一 task_id，p95 9.34ms |
| 准入性能 | 通过 | k6：20 req/s、15 秒稳态加 10 秒回落；399 请求零失败，p95 9.89ms |
| RabbitMQ 故障恢复 | 通过 | 故障期间任务返回 202；恢复后对应 Outbox `published_at` 非空，重试 6 次 |

原始 k6 摘要：`artifacts/p7-idempotency-summary.json`、
`artifacts/p7-admission-summary.json`。本地临时凭据未写入该记录。

## 未替代的生产门槛

本记录不代表多副本生产集群验收。仍需在预发/生产拓扑完成至少 60 分钟
soak、逐副本滚动重启、跨节点 RabbitMQ 故障演练、镜像漏洞扫描/SBOM 归档，
并按 `docs/slo-and-release-acceptance.md` 保存版本、容量和告警证据。
