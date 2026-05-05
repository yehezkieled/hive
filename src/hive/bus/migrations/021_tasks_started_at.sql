-- Sprint 24 phase 2 — W3 dashboard CFD per-bucket counts.
-- Capture the moment a task transitions out of `pending` so the CFD chart
-- can plot in_progress vs completed bands by their actual transition time
-- rather than a linear ramp from `created_at`.

ALTER TABLE tasks
    ADD COLUMN IF NOT EXISTS started_at TIMESTAMPTZ;

-- Best-effort backfill: pre-Sprint-24 tasks lose true transition timing
-- but we approximate from `created_at` so the chart isn't blank for
-- historical rows. New tasks (post-migration) get the real timestamp
-- via TaskStore.claim_next.
UPDATE tasks
SET started_at = created_at
WHERE status IN ('in_progress', 'completed', 'cancelled')
  AND started_at IS NULL;

CREATE INDEX IF NOT EXISTS idx_tasks_started_at ON tasks (started_at)
    WHERE started_at IS NOT NULL;
