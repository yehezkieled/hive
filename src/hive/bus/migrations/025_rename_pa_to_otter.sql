-- 025_rename_pa_to_otter.sql — rename the default maestro from `pa` to `otter`.
--
-- Brand/identity rename (the user's dog is named Otter, and `pa` was a
-- generic placeholder). Touches every column that stores an entity
-- name as TEXT so the running DB doesn't end up with a stranded `pa`
-- row plus a fresh `otter` row.
--
-- Idempotent — if `pa` was never registered, every UPDATE matches zero
-- rows. If this migration ever ran twice, the second run is a no-op.

UPDATE entities      SET name        = 'otter' WHERE name        = 'pa';
UPDATE entities      SET parent_name = 'otter' WHERE parent_name = 'pa';

UPDATE messages      SET sender      = 'otter' WHERE sender      = 'pa';
UPDATE messages      SET recipient   = 'otter' WHERE recipient   = 'pa';

UPDATE token_usage   SET entity_name = 'otter' WHERE entity_name = 'pa';

UPDATE audit_log     SET actor       = 'otter' WHERE actor       = 'pa';
UPDATE audit_log     SET target      = 'otter' WHERE target      = 'pa';

UPDATE advisor_calls SET entity_name = 'otter' WHERE entity_name = 'pa';
