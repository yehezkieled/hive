CREATE TABLE IF NOT EXISTS mode_requests (
    id BIGSERIAL PRIMARY KEY,
    requester TEXT NOT NULL,
    requested_mode TEXT NOT NULL,
    approver TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    reason TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    resolved_at TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_mode_requests_approver_status
    ON mode_requests (approver, status);

CREATE INDEX IF NOT EXISTS idx_mode_requests_requester
    ON mode_requests (requester, status);
