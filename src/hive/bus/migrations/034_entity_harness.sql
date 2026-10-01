-- Ticket T015 (ADR 0001): which Harness runs each entity. 'claude-code' keeps
-- every existing row on the adapter it already uses; 'codex' selects the Codex
-- adapter.

ALTER TABLE entities
    ADD COLUMN IF NOT EXISTS harness TEXT NOT NULL DEFAULT 'claude-code';
