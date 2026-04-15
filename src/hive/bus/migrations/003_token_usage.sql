-- Token usage table: one row per completed Claude session send_prompt call.
-- Sprint 2b records the usage sub-object from the claude -p stream-json
-- result event. The Max plan covers the cost; stored values are API-equivalent
-- (useful for accountability, not actual money spent).
--
-- Column names mirror the Anthropic API `usage` response exactly so the
-- session-parsing code can pass the dict straight through without renaming.

CREATE TABLE token_usage (
    id BIGSERIAL PRIMARY KEY,
    entity_name TEXT NOT NULL,
    session_id TEXT,
    model TEXT NOT NULL,
    input_tokens INTEGER NOT NULL DEFAULT 0,
    output_tokens INTEGER NOT NULL DEFAULT 0,
    cache_creation_input_tokens INTEGER NOT NULL DEFAULT 0,
    cache_read_input_tokens INTEGER NOT NULL DEFAULT 0,
    cost_usd NUMERIC(14, 8),
    recorded_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_token_usage_entity_time ON token_usage (entity_name, recorded_at DESC);
CREATE INDEX idx_token_usage_recorded_at ON token_usage (recorded_at DESC);
