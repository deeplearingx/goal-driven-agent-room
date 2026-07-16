CREATE TABLE IF NOT EXISTS webhook_subscriptions (
    subscription_id text PRIMARY KEY,
    tenant_id text NOT NULL REFERENCES tenants(tenant_id) ON DELETE CASCADE,
    name text NOT NULL,
    kind text NOT NULL CHECK (kind IN ('generic','feishu')),
    event_types text[] NOT NULL,
    endpoint_host text NOT NULL,
    endpoint_encrypted bytea NOT NULL,
    signing_secret_encrypted bytea,
    enabled boolean NOT NULL DEFAULT true,
    created_by text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (tenant_id, name),
    CHECK (cardinality(event_types) BETWEEN 1 AND 32)
);

CREATE INDEX IF NOT EXISTS webhook_subscriptions_events_idx
    ON webhook_subscriptions USING gin(event_types) WHERE enabled;

CREATE TABLE IF NOT EXISTS webhook_deliveries (
    delivery_id bigserial PRIMARY KEY,
    subscription_id text NOT NULL REFERENCES webhook_subscriptions(subscription_id) ON DELETE CASCADE,
    tenant_id text NOT NULL REFERENCES tenants(tenant_id) ON DELETE CASCADE,
    event_message_id text NOT NULL,
    event_type text NOT NULL,
    payload jsonb NOT NULL,
    status text NOT NULL DEFAULT 'pending' CHECK (status IN ('pending','processing','delivered','dead')),
    attempts integer NOT NULL DEFAULT 0,
    next_attempt_at timestamptz NOT NULL DEFAULT now(),
    locked_until timestamptz,
    response_status integer,
    last_error text,
    delivered_at timestamptz,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (subscription_id, event_message_id)
);

CREATE INDEX IF NOT EXISTS webhook_deliveries_claim_idx
    ON webhook_deliveries(next_attempt_at, delivery_id)
    WHERE status IN ('pending','processing');
