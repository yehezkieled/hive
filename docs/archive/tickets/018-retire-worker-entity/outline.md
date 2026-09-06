# Outline — Ticket 018: Retire the persistent Worker entity

Implementation structure for the 6-slice fan-out in [`design.md`](design.md).
One slice = one branch = one PR. Wave 1 (A–E) is parallel; Wave 2 (F) follows.

## Slice independence (why each leaves CI green)

A *peel* removes code that **uses** Worker without touching the class
definition, so the tree still imports and tests still pass:

- **A** deletes the dead `spawn_worker` chain. Nothing reachable called it
  (016 denied creation), so removing it changes no live behaviour. The Worker
  *class* still exists → imports intact.
- **B** deletes the `/worker` command. Its only arm (`kill`) calls the generic
  `kill_entity`; no Worker-class reference is required to remove the command.
- **C** deletes worker branches from the permission matrix. Role-string
  branches, isolated from Lead↔Maestro rules (research §C) → removing them
  can't break the surviving roles.
- **D** deletes worker serialization from the dashboard. 017 already moved the
  UI to lead/run counts; the `/api/org` `workers` array has no live consumer.
- **E** edits docs only. No code.
- **F** is the **only** slice that touches the class. It removes `worker.py`
  and *every remaining* reference in one commit — the sole point where the
  tree would break if done partially.

Blocker: **A → F** (F needs the constructor `lifecycle.spawn_worker` already
gone). B/C/D/E carry no logical blockers; sequence them in Wave 1 only to keep
F's diff small.

## Per-slice build steps

### A — spawn-action-chain peel
1. `actions.py`: delete `_SPAWN_WORKER_REQUIRED` (`:84`) + the `spawn_worker`
   parse branch (`:280-298`).
2. `permissions.py`: delete `can_spawn_worker` (`:137-143`); `manager.py:26`:
   delete the vestigial re-export.
3. `message_dispatcher.py`: delete the `spawn_worker` branch (`:489-575`), its
   import (`:30`), and the `_last_spawned_workers` reset (`:307`).
4. `lifecycle_manager.py`: delete `spawn_worker()` (`:352-440`).
5. `manager.py`: delete `spawn_worker()` facade (`:392-402`),
   `_last_spawned_workers` init (`:134`).
6. `config.py:205`: drop `spawn_worker` from the docstring.
7. Tests: DELETE `test_process_manager.py::test_spawn_worker*` &
   `TestMaxWorkersEnforcement`, `test_lifecycle_manager.py:399-421`; REDEFINE
   the `*_spawn_worker_denied*` + `TestSpawnWorkerAction` + `TestSpawnWorker
   Permissions` to assert the action is now unknown/rejected (not specially
   denied).

### B — /worker command peel
1. `commands/dispatch.py`: remove `worker` from `KNOWN_COMMANDS` (`:84`), the
   dispatch arm (`:275-276`), and `_execute_worker` (`:686-706`).
2. `telegram/commands.py:76`: remove the targeted-command entry.
3. `telegram/help_text.py:113-118`: remove `HELP_TEXT['worker']`.
4. Tests: REDEFINE `test_commands.py` worker arms — the command is gone (assert
   unknown-command), keep `/kill` and `/swarm` coverage intact.

### C — permission-matrix peel
1. `permissions.py`: remove worker branches in `can_message` (`:53-54,65-67`),
   `cc_targets_for` (`:98-103`), `can_request_decision` (`:118-120`).
2. Tests: trim worker rows from `test_permissions.py` / `test_peer_messaging.py`
   (keep Lead/Maestro coverage).

### D — dashboard peel
1. `web/app.py:99-105`: drop the `team.workers` serialization from `api_org`.
2. `web/view_model.py`: remove residual worker comments (`:80-82,105,164`);
   `m.leads` / `m.active_runs` already correct.
3. `web/templates/_macros.html:91-93`, `landing.html:738`: drop worker
   count comment + `/a:…worker` alias.
4. Tests: adjust any web test asserting `workers` in the org payload.

### E — glossary + docs peel
1. `CONTEXT.md`: replace the Worker entry with the D2 tombstone; fix
   `Team = Lead + Workers` → `Lead + Workflow runs` (`:20,:40-44,:174`); update
   illustrative mentions (`:60,:107,:184-185`).
2. `README.md:5`, `docs/DEPLOYMENT.md:36,555-557,567-570`: drop/clarify Worker
   references. **Do not touch ADRs.**

### F — core type deletion (atomic) + persistence guard
1. `rm src/hive/models/worker.py`.
2. Remove the 6 `import Worker` lines (`entity_store`, `lifecycle_manager`,
   `manager`, `message_dispatcher`, `approval_handler`, `commands/dispatch`).
3. Role registries: drop `"worker"` from `entity.py:308` + docstrings
   (`:198,201`), `loops.py:8` `_VALID_ROLES`, `claude_adapter.py:109` tuple;
   change `claude_adapter.py:64` default off `"worker"`.
4. Remove every `isinstance(…, Worker)` guard (research §F list), preserving
   surrounding logic (e.g. `dispatch.py:_format_org` keeps the `team.workers`
   loop, drops the type check; `entity_store._row_to_entity` drops the worker
   reconstruction branch; `manager._reconstruct_teams` drops worker linking).
5. **D3 guard:** add idempotent `DELETE FROM entities WHERE role='worker'` to
   the startup restore path; verify a seeded stray row is gone after restart.
6. Tests: delete/repoint every remaining Worker-constructing test
   (`test_team.TestWorkerFields`, `test_entity_store.test_load_worker_*`,
   `test_role_jd` worker cases, `integration/test_lead_worker_roundtrip`).
7. Optionally remove `personalities/role-worker.md` (orphaned once no role
   loads it) — confirm no loader path references it post-step-3.

## Build order

```
 Wave 1 (parallel, AFK auto-merge on green CI):  A · B · C · D · E
 Wave 2 (after A merges; ideally after all):      F
 Then: ticket-level verify + deploy + shared 016/018 live smoke.
```
