-- Sprint 12 Phase 4 — auto-recovery on task failures.
-- Tasks gain retry bookkeeping: after each failure the manager increments
-- retry_count and re-dispatches the prompt with the failure reason
-- prepended, up to max_retries. At that point the task escalates up the
-- hierarchy (worker -> lead -> maestro -> user via Telegram).

ALTER TABLE tasks
    ADD COLUMN IF NOT EXISTS retry_count INTEGER NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS max_retries INTEGER NOT NULL DEFAULT 3,
    ADD COLUMN IF NOT EXISTS failure_reason TEXT;
