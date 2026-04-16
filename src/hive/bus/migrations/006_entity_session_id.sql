-- Sprint 3a: persist the Claude CLI session_id so entities can resume
-- conversations across one-shot calls via --resume <session_id>.
ALTER TABLE entities ADD COLUMN IF NOT EXISTS session_id TEXT;
