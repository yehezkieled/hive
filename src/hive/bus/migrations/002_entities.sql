-- Entities table: persistent organizational structure for the orchestrator.
-- Sprint 2a restores entities on startup in IDLE state — this table tracks
-- the roster of registered entities, not their live subprocesses.

CREATE TABLE entities (
    name TEXT PRIMARY KEY,
    role TEXT NOT NULL,
    state TEXT NOT NULL,
    model TEXT NOT NULL,
    personality_path TEXT,
    pid INTEGER,
    started_at TIMESTAMPTZ,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_entities_role ON entities (role);
CREATE INDEX idx_entities_state ON entities (state);
