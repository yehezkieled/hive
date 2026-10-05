-- Cut-over (ADR 0033): the Maestro / Team Lead runtime is retired. Firstmate
-- supervises work now; Hive keeps only the Vault entity for the payment
-- approval rail.
--
-- DATA LOSS (intentional, irreversible): every entities row that is not the
-- vault is deleted (maestros, team leads, any leftover workers), the projects
-- registry is dropped, and the columns that only served the retired runtime
-- are dropped. vault_actions, mode_requests, audit_log, token_usage, tasks and
-- messages are NOT touched.
DELETE FROM entities WHERE role <> 'vault';

DROP TABLE IF EXISTS projects;

DROP INDEX IF EXISTS idx_entities_parent;
ALTER TABLE entities
    DROP COLUMN IF EXISTS parent_name,
    DROP COLUMN IF EXISTS team_name,
    DROP COLUMN IF EXISTS worktree_path,
    DROP COLUMN IF EXISTS task_id,
    DROP COLUMN IF EXISTS awaiting_decision,
    DROP COLUMN IF EXISTS confirmed_with_user,
    DROP COLUMN IF EXISTS phase_confirm,
    DROP COLUMN IF EXISTS last_decision_question;
