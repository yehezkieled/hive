-- Sprint 3a: hierarchy columns for team/worker parent tracking.
-- parent_name: for leads it's the maestro; for workers it's the lead.
-- team_name: the team this entity belongs to (leads and workers only).
ALTER TABLE entities ADD COLUMN IF NOT EXISTS parent_name TEXT;
ALTER TABLE entities ADD COLUMN IF NOT EXISTS team_name TEXT;
CREATE INDEX IF NOT EXISTS idx_entities_parent
    ON entities (parent_name) WHERE parent_name IS NOT NULL;
