-- 025_rename_pa_to_otter.sql
-- Idempotent rename: 'pa' -> 'otter' across all tables that store entity names.
-- Exact match: WHERE col = 'pa' renames to 'otter'.
-- Prefix match: WHERE col LIKE 'pa.%' rewrites the 'pa.' prefix to 'otter.'.
-- Second run touches zero rows with no errors.

-- entities.name
UPDATE entities SET name = 'otter' WHERE name = 'pa';
UPDATE entities SET name = 'otter.' || substring(name FROM 4) WHERE name LIKE 'pa.%';

-- entities.parent_name
UPDATE entities SET parent_name = 'otter' WHERE parent_name = 'pa';
UPDATE entities SET parent_name = 'otter.' || substring(parent_name FROM 4) WHERE parent_name LIKE 'pa.%';

-- messages.sender
UPDATE messages SET sender = 'otter' WHERE sender = 'pa';
UPDATE messages SET sender = 'otter.' || substring(sender FROM 4) WHERE sender LIKE 'pa.%';

-- messages.recipient
UPDATE messages SET recipient = 'otter' WHERE recipient = 'pa';
UPDATE messages SET recipient = 'otter.' || substring(recipient FROM 4) WHERE recipient LIKE 'pa.%';

-- token_usage.entity_name
UPDATE token_usage SET entity_name = 'otter' WHERE entity_name = 'pa';
UPDATE token_usage SET entity_name = 'otter.' || substring(entity_name FROM 4) WHERE entity_name LIKE 'pa.%';

-- audit_log.actor
UPDATE audit_log SET actor = 'otter' WHERE actor = 'pa';
UPDATE audit_log SET actor = 'otter.' || substring(actor FROM 4) WHERE actor LIKE 'pa.%';

-- audit_log.target
UPDATE audit_log SET target = 'otter' WHERE target = 'pa';
UPDATE audit_log SET target = 'otter.' || substring(target FROM 4) WHERE target LIKE 'pa.%';

-- advisor_calls.entity_name
UPDATE advisor_calls SET entity_name = 'otter' WHERE entity_name = 'pa';
UPDATE advisor_calls SET entity_name = 'otter.' || substring(entity_name FROM 4) WHERE entity_name LIKE 'pa.%';
