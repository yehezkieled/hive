-- Tasks table: simple persistent work items tracked by Hive.
-- Sprint 2b ships minimal CRUD (create, list, get, update_status). Workers
-- don't claim from this queue yet — that lands in Sprint 3 alongside
-- SELECT ... FOR UPDATE SKIP LOCKED when there's something to consume them.
--
-- No FK to entities(name): tasks outlive entity deletes, and the cascade
-- semantics aren't obvious enough yet to commit to them. Treat assigned_to
-- as a free-text soft reference.

CREATE TABLE tasks (
    id BIGSERIAL PRIMARY KEY,
    title TEXT NOT NULL,
    description TEXT,
    status TEXT NOT NULL DEFAULT 'pending',   -- pending | in_progress | completed | cancelled
    priority INTEGER NOT NULL DEFAULT 3,      -- 0 (urgent) .. 4 (backlog)
    assigned_to TEXT,                         -- entity name, nullable
    created_by TEXT NOT NULL,                 -- "user:<tg_id>" | "system" | "entity:<name>"
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    completed_at TIMESTAMPTZ
);

CREATE INDEX idx_tasks_status ON tasks (status, priority, created_at);
CREATE INDEX idx_tasks_assigned ON tasks (assigned_to) WHERE assigned_to IS NOT NULL;
