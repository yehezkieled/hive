-- Ticket 019 (ADR 0019): the code-enforced phase-confirmation gate. A maestro
-- cannot spawn_team until it has completed one request_decision->user->reply
-- round-trip. ``confirmed_with_user`` is that durable floor; ``phase_confirm`` is
-- the per-maestro opt-out (off => an unattended maestro skips the gate).
-- Grandfather every maestro that already exists so deploying 019 does not
-- retroactively block its next spawn (only maestros created afterward gate).
ALTER TABLE entities
    ADD COLUMN IF NOT EXISTS confirmed_with_user BOOLEAN NOT NULL DEFAULT FALSE;
ALTER TABLE entities
    ADD COLUMN IF NOT EXISTS phase_confirm BOOLEAN NOT NULL DEFAULT TRUE;
UPDATE entities SET confirmed_with_user = TRUE WHERE role = 'maestro';
