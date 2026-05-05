-- Sprint 24 phase 1 — W2 dashboard health probes.
-- Rolling 2-hour retention; the widget displays the last 60 1-minute samples.

CREATE TABLE IF NOT EXISTS health_log (
    id BIGSERIAL PRIMARY KEY,
    subsystem TEXT NOT NULL,
    status TEXT NOT NULL,           -- 'ok' | 'warn' | 'crit'
    summary TEXT,
    ts TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_health_log_subsystem_ts
    ON health_log (subsystem, ts DESC);
