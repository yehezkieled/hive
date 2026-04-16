CREATE TABLE IF NOT EXISTS vault_actions (
    id BIGSERIAL PRIMARY KEY,
    vault_name TEXT NOT NULL,
    description TEXT NOT NULL,
    requester TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    resolved_at TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_vault_actions_vault
    ON vault_actions (vault_name, status);
