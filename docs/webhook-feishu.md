# Webhook and Feishu delivery

Webhook subscriptions are tenant-scoped control-plane resources. The endpoint
URL and signing secret are encrypted before PostgreSQL storage; list/create
responses expose only the endpoint host.

```bash
curl -X POST "$GATEWAY_URL/api/v1/admin/webhook-subscriptions" \
  -H "Authorization: Bearer $GATEWAY_API_KEY" \
  -H 'Content-Type: application/json' \
  -d '{
    "name":"ops-alerts",
    "kind":"feishu",
    "event_types":["task_finished","task_error"],
    "endpoint_url":"https://open.feishu.cn/open-apis/bot/v2/hook/REDACTED",
    "signing_secret":"REDACTED"
  }'
```

Run the delivery worker with the production webhook profile:

```bash
GATEWAY_CONFIG_ENCRYPTION_KEY=... \
GATEWAY_WEBHOOK_ALLOWED_HOSTS=open.feishu.cn,hooks.example.com \
docker compose -f docker-compose.production.yml --profile webhook up -d
```

Every projected event creates a tenant-local delivery in the same PostgreSQL
transaction. Workers claim with `FOR UPDATE SKIP LOCKED`, retry transient
failures with persisted exponential backoff, and mark an item `dead` after 12
attempts. Generic endpoints receive the event envelope and optional
`X-Agent-Room-Signature: sha256=...`; the signed input is
`timestamp + "." + raw body`.

Feishu receives a text custom-bot payload. When a signing secret is configured,
the worker generates `sign` from HMAC-SHA256 of the empty payload using
`timestamp + "\n" + secret` as the key, as required by the official custom-bot
protocol. The worker also applies the documented 5 requests/second cap and
limits responses to 64 KiB. [Official Feishu guide](https://open.feishu.cn/document/ukTMukTMukTM/ucTM5YjL3ETO24yNxkjN)

For SSRF control, endpoints must be HTTPS and match
`GATEWAY_WEBHOOK_ALLOWED_HOSTS`; redirects, IP-literal endpoints and DNS
results that resolve only to non-public addresses are rejected.
