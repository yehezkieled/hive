ALTER TABLE entities ADD COLUMN codex_usage JSONB NOT NULL DEFAULT '{}'::jsonb;
