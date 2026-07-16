# Redis configuration cache and invalidation

Gateways use Redis as a shared L2 cache and a bounded in-process L1 cache for
configuration reads that occur on the control-plane hot path:

- `GET /api/v1/admin/prompt-releases`
- runtime snapshot resolution during task admission

The local cache has a TTL and a hard entry limit. Redis is optional in local
development: a cache outage falls back to PostgreSQL reads. When `REDIS_URL` is
configured in production, Gateway startup verifies Redis and fails closed if it
is unavailable; this prevents a multi-instance deployment from silently losing
configuration consistency.

Every Prompt Release write and Runtime Snapshot creation deletes the shared key
and publishes an event to `agent_room:config:invalidate:v1`. Each Gateway has a
long-lived subscriber that evicts its local copy immediately. TTL remains a
bounded-staleness fallback for a temporary Pub/Sub disconnect.

```yaml
# docker-compose.production.yml configures these defaults
REDIS_URL: redis://redis:6379/0
GATEWAY_CONFIG_CACHE_TTL: 30s
GATEWAY_CONFIG_CACHE_LOCAL_LIMIT: 2048
```

The production Compose file includes Redis with AOF enabled and makes Gateway
wait for its health check. Run it normally with:

```bash
docker compose -f docker-compose.production.yml up -d
```

Redis carries only serialized immutable snapshots and release routing metadata;
provider secrets, webhook URLs, and signing keys remain encrypted in
PostgreSQL and are never placed in cache values or Pub/Sub messages.
