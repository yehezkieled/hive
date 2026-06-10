-- Ticket 013 / ADR 0009: the custom advisor is retired in favour of Claude
-- Code's native /advisor, so its telemetry table is no longer written.
-- Migration 015 (CREATE) and 025 (the otter rename UPDATE) stay untouched —
-- applied history is append-only; this forward migration drops the table.
DROP TABLE IF EXISTS advisor_calls;
