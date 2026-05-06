-- Sprint 25 Phase 1 — extend vault_actions with structured payment fields.
-- Existing Sprint 6 rows get action_type='generic' (set by DEFAULT) and NULL
-- payment fields, so the original free-text approval flow keeps working.

ALTER TABLE vault_actions
    ADD COLUMN IF NOT EXISTS action_type TEXT NOT NULL DEFAULT 'generic',
    ADD COLUMN IF NOT EXISTS amount_cents BIGINT,
    ADD COLUMN IF NOT EXISTS currency TEXT DEFAULT 'USD',
    ADD COLUMN IF NOT EXISTS recipient TEXT,
    ADD COLUMN IF NOT EXISTS idempotency_key TEXT,
    ADD COLUMN IF NOT EXISTS payload JSONB DEFAULT '{}'::jsonb,
    ADD COLUMN IF NOT EXISTS executed_at TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS execution_result JSONB,
    ADD COLUMN IF NOT EXISTS denial_reason TEXT;

CREATE UNIQUE INDEX IF NOT EXISTS idx_vault_actions_idempotency
    ON vault_actions(idempotency_key)
    WHERE idempotency_key IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_vault_actions_status_created
    ON vault_actions(status, created_at);
