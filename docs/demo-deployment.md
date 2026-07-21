# Public AI Demo deployment (DNS + HTTPS)

This runbook deploys the **same React console, Go Gateway, RabbitMQ and Python
LangGraph Worker** that the repository tests. It is intentionally a small,
invite-only portfolio deployment for one Linux VM; Kubernetes remains the
multi-replica production path.

The public URL after completion is:

```text
https://<your-domain>/app/
```

## 1. Prerequisites

- A domain you control and a Linux VM with a public IPv4 address. A 2 vCPU /
  4 GB RAM VM is sufficient for a short-lived demo with one or two Workers;
  model calls, not CPU, are the main latency/cost driver.
- Ubuntu 22.04+ (or equivalent), Docker Engine and Docker Compose v2.
- Inbound TCP `80` and `443` opened in both the cloud security group and host
  firewall. Do **not** expose PostgreSQL, RabbitMQ or Gateway port `8080`.
- One provider credential (`OPENAI_API_KEY` or an Anthropic-compatible route).

## 2. DNS first

At your DNS provider create these records before starting Caddy:

| Record | Name | Value | Why |
| --- | --- | --- | --- |
| A | `demo` (or `@`) | VM public IPv4 | Lets Let's Encrypt validate HTTPS. |
| AAAA | same name | VM public IPv6 | Only when IPv6 is configured and reachable. |

Use a low TTL such as 300 seconds during setup. Verify from a network outside
the VM:

```bash
dig +short demo.example.com A
curl -I http://demo.example.com
```

The first command must return the VM address. Caddy uses port 80 for ACME's
HTTP challenge and then obtains/renews the certificate automatically.

## 3. Configure secrets without putting them in Git

```bash
git clone <YOUR_GITHUB_REPOSITORY_URL> agent-room
cd agent-room
cp .env.demo.example .env.demo
```

Generate long random values on the VM:

```bash
openssl rand -hex 32                 # Gateway API key
openssl rand -base64 32              # Gateway configuration encryption key
openssl rand -base64 32              # database / RabbitMQ passwords
docker run --rm caddy:2.10.2-alpine \
  caddy hash-password --plaintext 'choose-a-long-demo-password'
```

Put the Caddy output in `DEMO_BASIC_AUTH_HASH` in `.env.demo`, retaining the
single quotes shown in the example. The bcrypt string contains `$`; the quotes
prevent Docker Compose from treating it as an environment substitution.

Set `CADDY_SITE` to the bare DNS name (for example `demo.example.com`) and
set one model provider key. Keep `GATEWAY_API_KEY` non-empty: the Caddy
container attaches it only to upstream requests, so it is never exposed in
the React bundle. `.env.demo` is gitignored and Docker also excludes it from
build context. Set `GATEWAY_CONFIG_ENCRYPTION_KEY` to the base64 output from
the second command; Compose validates it even when optional Go worker profiles
are disabled.

## 4. Validate and start

```bash
docker compose --env-file .env.demo \
  -f docker-compose.production.yml \
  -f docker-compose.demo.yml config --quiet

docker compose --env-file .env.demo \
  -f docker-compose.production.yml \
  -f docker-compose.demo.yml up -d --build --scale worker=2

docker compose --env-file .env.demo \
  -f docker-compose.production.yml \
  -f docker-compose.demo.yml ps
```

Open `https://demo.example.com/app/`. The browser first prompts for the demo
Basic Auth credentials, then uses same-origin `/tasks/*` SSE calls. Caddy
proxies those requests to the Go Gateway with streaming flush enabled, so
tokens and tool events are not buffered by the reverse proxy.

Verify certificate and health:

```bash
curl -I https://demo.example.com/healthz
curl -I https://demo.example.com/app/
docker compose --env-file .env.demo \
  -f docker-compose.production.yml \
  -f docker-compose.demo.yml logs --tail=100 demo gateway worker
```

## 5. Demonstrate the AI path

In `/app/`, submit a bounded coding request such as:

> Create a Python function `normalize_email`, write tests, and explain the
> validation choices.

The UI shows the persisted SSE sequence: task admission → planner → developer
token/tool calls → reviewer → delivery. In the public overlay, direct shell
is disabled and mutating tools remain approval-gated. Do not enable
`AGENT_ROOM_ALLOW_DIRECT_SHELL` on this credential-bearing VM.

## 6. Operations and rollback

- Check `https://<domain>/readyz`, DLQ depth and outbox age before sharing the
  link. The detailed alerting and restore procedure is in
  [`production-runbook.md`](production-runbook.md).
- To deploy a new revision, build first, then recreate only application
  services; preserve named PostgreSQL/RabbitMQ volumes.
- To roll back application code, deploy the previously recorded image digest.
  Never roll a database migration backward destructively.
- This Basic Auth layer is appropriate for a small interview demo only. A
  real public product should use OIDC/WAF/rate limiting, managed PostgreSQL,
  a three-node RabbitMQ cluster and the Kubernetes baseline.
