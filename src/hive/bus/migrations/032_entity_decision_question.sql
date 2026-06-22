-- Ticket 038: the maestro→user decision channel on the web. last_decision_question
-- stores the free-text question a maestro asked via request_decision, alongside
-- awaiting_decision (Ticket 029), so the web decision bubble can render it and
-- GET /api/decisions/pending can re-show it after a reload (SSE is best-effort).
-- Nullable, set on ask, nulled on unpark. The channel is one-deep per maestro, so
-- a single column — not a separate store — holds the whole question state
-- (ADR 0024).

ALTER TABLE entities
    ADD COLUMN IF NOT EXISTS last_decision_question TEXT;
