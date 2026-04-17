-- Sprint 10: Track last activity time for auto-kill-idle feature.
ALTER TABLE entities ADD COLUMN IF NOT EXISTS last_activity_at TIMESTAMPTZ;
