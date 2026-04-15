-- Messages table: durable log of every message routed through the bus.
-- Replaces the Sprint 1 SQLite schema.

CREATE TABLE messages (
    id BIGSERIAL PRIMARY KEY,
    sender TEXT NOT NULL,
    recipient TEXT NOT NULL,
    content TEXT NOT NULL,
    timestamp TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    status TEXT NOT NULL DEFAULT 'pending',
    conversation_id TEXT,
    metadata JSONB
);

CREATE INDEX idx_messages_recipient ON messages (recipient, status);
CREATE INDEX idx_messages_conversation ON messages (conversation_id);
CREATE INDEX idx_messages_timestamp ON messages (timestamp DESC);
