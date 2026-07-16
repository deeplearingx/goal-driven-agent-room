ALTER TABLE outbox ADD COLUMN IF NOT EXISTS next_attempt_at timestamptz;
CREATE INDEX IF NOT EXISTS outbox_retry_idx ON outbox (next_attempt_at) WHERE published_at IS NULL;
