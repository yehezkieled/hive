-- Audit log: a single journal of every command issued + every state
-- transition ProcessManager drives. One row per event. The cost is one
-- insert per event; over-logging is easier to filter later than
-- under-logging is to reconstruct.
--
-- `action` is namespaced so a single LIKE-prefix filter gives us
-- per-category readouts without a second column:
--   command.<name>   — every /command dispatched through the bridge
--   entity.<state>   — spawn | kill | error | dead
--   task.<op>        — create | update_status
--
-- `actor` follows the same namespaced convention:
--   user:<telegram_id> | system | entity:<name>

CREATE TABLE audit_log (
    id BIGSERIAL PRIMARY KEY,
    actor TEXT NOT NULL,
    action TEXT NOT NULL,
    target TEXT,
    details JSONB,
    timestamp TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_audit_log_timestamp ON audit_log (timestamp DESC);
CREATE INDEX idx_audit_log_actor_time ON audit_log (actor, timestamp DESC);
CREATE INDEX idx_audit_log_action ON audit_log (action);
