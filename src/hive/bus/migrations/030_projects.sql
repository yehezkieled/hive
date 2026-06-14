-- Projects table: the project registry for ownership (Ticket 024).
-- One project maps to at most one owning maestro. owning_maestro is a plain
-- TEXT column (no FK to entities) so the store stays independent of the
-- EntityStore in tests. The partial UNIQUE index is the DB backstop for the
-- "1 project <-> <=1 maestro" invariant.

CREATE TABLE projects (
    name           TEXT PRIMARY KEY,
    root_path      TEXT NOT NULL UNIQUE,
    owning_maestro TEXT,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at     TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE UNIQUE INDEX projects_owning_maestro_uniq
    ON projects (owning_maestro)
    WHERE owning_maestro IS NOT NULL;
