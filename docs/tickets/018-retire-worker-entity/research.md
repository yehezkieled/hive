# Research — Ticket 018: Retire the persistent Worker entity

Source: a parallel multi-modal code sweep (7 area-mappers + 1 completeness
critic, 2026-06-13). Every site below carries a `file:line` ref. The sweep's
job was to find *where* Worker lives; the design calls (slice shape, glossary,
persistence) are deferred to `design.md` and flagged **DECISION** here.

## Headline

The Worker footprint is **~55 code sites + ~40 test functions across ~25
files**. But the load-bearing finding is about **shape, not size**:

> **Worker is an isolated `Entity` subclass selected by a load-bearing role
> *string* (`role == "worker"`), never by `isinstance` in the
> role-sensitive paths. Maestro / Lead / Vault are unaffected by deleting the
> class — provided the role-string branches are removed in lockstep.**

That makes 018 a clean subtraction, not surgery on shared abstractions. The
one hard constraint is Python import atomicity (below).

## The atomicity constraint (drives the lane)

`Worker` is `import`-ed in **6 source files** (`entity_store.py:19`,
`lifecycle_manager.py:34`, `manager.py:53`, `message_dispatcher.py:35`,
`approval_handler.py:22`, `commands/dispatch.py:24`) and `isinstance`-checked
in **~15 places**. You **cannot** merge "delete `models/worker.py`" while any
file still imports it — the tree fails to import, CI red. So the **type
deletion is one atomic unit**: `worker.py` + all 6 imports + every
`isinstance(…, Worker)` guard + the persistence reconstruction + the role
registries, in a single commit.

What *can* land independently before it are the **dead-behaviour peels** —
code that *uses* Worker but whose removal doesn't touch the class definition.
Each peel leaves the system green and shrinks the atomic core.

## Surface by subsystem

### A. Dead `spawn_worker` action chain — PEEL (independent)
The creation path 016 already made unreachable. Pure dead code.
- `bus/actions.py:84` `_SPAWN_WORKER_REQUIRED`, `:280-298` parse branch →
  delete.
- `bus/permissions.py:137-143` `can_spawn_worker()` (unconditional `False`) +
  `manager.py:26` vestigial re-export → delete.
- `process/message_dispatcher.py:489-575` the denial+spawn branch (498-520
  reachable denial, 521-575 unreachable fallback — comment says "018 deletes
  the whole branch") + `:30` import + `:307` introspection reset → delete.
- `process/lifecycle_manager.py:352-440` `spawn_worker()` (the constructor) →
  delete.
- `process/manager.py:392-402` `spawn_worker()` facade + `:134`
  `_last_spawned_workers` introspection list + `:564` append → delete.
- `config.py:205` docstring mentioning `spawn_worker` → edit.

### B. `/worker` command — PEEL (independent)
016 removed the `spawn` arm; only `kill` survives "until 018."
- `commands/dispatch.py:84` `KNOWN_COMMANDS` entry, `:275-276` dispatch arm,
  `:686-706` `_execute_worker` (kill handler) → delete.
- `telegram/commands.py:76` targeted-command registration → delete.
- `telegram/help_text.py:113-118` `HELP_TEXT['worker']` → delete.
- **Survives:** the generic `/kill <entity>` command (distinct from `/worker
  kill`) and `/swarm` — they operate on the team, not the Worker class.

### C. Permission-matrix worker branches — PEEL (independent)
Role-string branches woven into otherwise-shared rules. Isolated from
Lead↔Maestro rules per the sweep.
- `bus/permissions.py` `can_message():53-54,65-67`, `cc_targets_for():98-103`,
  `can_request_decision():118-120` → edit-remove the `worker` branches.

### D. Dashboard / web UI — PEEL (independent, mostly already migrated by 017)
- `web/app.py:99-105` `api_org()` serializes `team.workers` → delete block.
  (No live UI consumes it; tests only assert the `maestros` key.)
- `web/view_model.py:132-151` already computes `m.leads` / `m.active_runs`
  (017 replaced the "W" worker count) → edit residual comments.
- `web/templates/_macros.html:91-93`, `landing.html:738` (`/a:…worker` alias)
  → edit/delete.

### E. Glossary + docs — PEEL (independent) · **DECISION**
- **Editable:** `CONTEXT.md` (Worker entry `:23-30`, Team def `:20,:40-44`,
  Relationships `:174`, examples `:60,:107,:184-185`), `README.md:5`,
  `docs/DEPLOYMENT.md:36,:555-557,:567-570`.
- **DO NOT TOUCH:** ADRs 0004/0008/0009/0010/0013/0014 reference Worker but are
  **append-only**. Leave them.
- **DECISION (Q10):** the `CONTEXT.md` "Worker" entry — delete outright, or
  tombstone-and-redirect to the existing **"Leaf agent"** term? Sweep
  recommends a one-line tombstone (`Worker — retired; see ADR 0013 / Leaf
  agent`) + fix "Team = Lead + Workers" → "Lead + Workflow runs."

### F. The atomic core — TYPE DELETION (one unit, lands last)
Everything that references the `Worker` *class* directly:
- `models/worker.py:12-31` → delete whole file.
- **6 imports** (list above) → delete.
- **Role registries** (critic finds): `models/entity.py:198,201` docstrings,
  `:308` `role in (…, "worker")` tuple; `process/loops.py:8` `_VALID_ROLES`;
  `runtime/claude_adapter.py:64` default `role="worker"`, `:109` role tuple.
- **`isinstance(…, Worker)` guards** in non-spawn paths:
  `entity_store.py:98-125,163-171` (factory helpers + `_row_to_entity` worker
  branch), `lifecycle_manager.py:266,481,491`, `manager.py:248,253,364,605`
  (`_reconstruct_teams`), `message_dispatcher.py:775`,
  `approval_handler.py:56-57,617`, `commands/dispatch.py:1204-1210`
  (`_format_org` — keep the `team.workers` loop, drop the `isinstance`).

### G. Persistence — **DECISION** (the orphaned-row risk, Q5/Q6)
- DB: `entities` table (`migrations/002,007,008`). The worker-bearing columns
  (`worktree_path`, `task_id`, `parent_name`, `team_name`) are **shared with
  TeamLead** → **do NOT drop columns**; Hive's schema is append-only anyway.
- Restore path: `entity_store.all()` → `_row_to_entity()` →
  `manager.restore()` → `rebuild_hierarchy()` (`__main__.py:270-276`).
- **The risk:** after the class is gone, a leftover `role='worker'` DB row
  hits `_row_to_entity`'s fallback (`:173` → `Entity(role='worker')`) — sweep
  calls it **"safe but lossy"**: no crash, but a zombie base-Entity with a
  now-invalid role.
- **DECISION:** rely on deploy-time `/worker kill` of stragglers (already in
  016's deploy plan) + the existing tolerant fallback, **or** add a one-time
  startup sweep that `DELETE`s `role='worker'` rows. Recommend: kill at deploy
  **and** a defensive `DELETE FROM entities WHERE role='worker'` guard so a
  missed straggler can't zombie-restore.

### H. The denial stubs after deletion — **DECISION** (Q4)
Once the `spawn_worker` parse branch (`actions.py:280-298`) **and** the
dispatcher branch are both deleted, a stale `spawn_worker` emission (only
possible from a hallucination — 016 trimmed every prompt) falls through to the
**generic unknown-action path**. **MUST CONFIRM IN CODE during design:** that
`parse_actions` rejects an unknown `type` *gracefully* (reject-with-feedback,
no crash). If it doesn't, keep a thin "retired action → reject" guard.

## Tests — three buckets (the sweep categorized all ~40)

| Bucket | Meaning | Examples | Action |
|--------|---------|----------|--------|
| **DELETE** | tests the deleted spawn/lifecycle machinery | `test_process_manager.py` `test_spawn_worker*`, `test_spawn_worker_creates_worktree`, `TestMaxWorkersEnforcement`; `test_lifecycle_manager.py:399-421` | remove with their code (same slice) |
| **REDEFINE** | tests `spawn_worker` *denial* — the drainage proof | `test_*_spawn_worker_denied`, `TestSpawnWorkerAction` (parse), `test_permissions.TestSpawnWorkerPermissions`, `integration/test_lead_worker_roundtrip` | repoint to assert Workflow is the path / unknown-action rejection |
| **KEEP→eventually drop** | tests Worker *structure/messaging/persistence* | `test_team.TestWorkerFields`, `test_peer_messaging` worker rules, `test_entity_store.test_load_worker_*`, `test_role_jd.test_loads_worker_jd` | these die **with** the core type-deletion slice (they construct `Worker`) |

**Vertical-slice rule:** each slice carries its own test changes — there is
**no** separate "tests" slice.

## Answers to `questions.md`

1. **Surface** → mapped above (~55 code + ~40 tests / ~25 files).
2. **Order** → peels (A–E) independent, any order; **core (F)** atomic and
   last (smallest once peels land). Persistence (G) rides the core.
3. **Shared base?** → No shared *abstraction* to cut; `Entity` base stays,
   role-string branches removed in lockstep. Clean.
4. **Denial stubs** → delete both parse + dispatch branches; **confirm**
   generic unknown-action rejection is graceful (else keep a thin guard). →
   **design DECISION H.**
5. **Persistence crash?** → No crash (tolerant fallback), but zombie rows
   possible → kill at deploy + defensive `DELETE` guard. → **DECISION G.**
6. **Live stragglers** → `/worker kill` at deploy *before* removing the
   command (slice ordering: kill, then peel B).
7. **`/worker` command** → entire command goes; `/kill` and `/swarm` survive.
8. **Dashboard** → already migrated by 017; removal leaves a coherent tree
   (Maestro + Lead count + Workflow-run cards).
9. **Tests** → three buckets above.
10. **Glossary** → tombstone-and-redirect to "Leaf agent". → **DECISION E.**
11. **Sequencing vs 016** → 018 shares 016's live DoD; one
    delete→deploy→maestro→lead→leaf smoke proves both.

## Provisional lane: **FAN-OUT**

Five independent dead-behaviour peels (A–E) + one atomic type-deletion core
(F, carrying G + its tests) = 6 slices, each a real PR. Blocker graph: peels
are parallel; **core is blocked by peel A** (its constructor
`lifecycle.spawn_worker` must go before the class) and best sequenced after
B–E to stay small. Finalised in `plan.md`.
