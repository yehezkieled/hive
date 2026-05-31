-- Ticket 003: reuse the pending-approval row for interactive gates.
-- A `kind` discriminator distinguishes a permission-mode elevation request
-- ('mode_request', the original meaning) from an interactive-gate decision
-- ('gate'). Existing rows are mode-elevation requests, so default to that.
ALTER TABLE mode_requests
    ADD COLUMN IF NOT EXISTS kind TEXT NOT NULL DEFAULT 'mode_request';

CREATE INDEX IF NOT EXISTS idx_mode_requests_kind_approver_status
    ON mode_requests (kind, approver, status);
