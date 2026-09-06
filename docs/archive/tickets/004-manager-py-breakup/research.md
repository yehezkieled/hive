# Research — `process/manager.py` god object

Investigated 2026-05-30 by an Explore agent during Phase 2 scoping.
All file/line references verified against `main` at that date.

## Size and shape

- **2,308 LOC** in a single file.
- **One class** (`ProcessManager`) plus 2 module-level helpers.
- **47 methods** on `ProcessManager`, mixing nine distinct
  responsibilities:
  1. Entity lifecycle (register, spawn, kill, restore hierarchy)
  2. Persistence and auditing (persist, audit, token-usage tracking)
  3. Team and Worker lifecycle (create_team, spawn_worker, kill)
  4. Message routing and action dispatch (send_to_entity, parse
     actions, handle message types)
  5. Wake-on-inbound scheduling (auto-spawn on inbound, rate
     limiting)
  6. Approval workflows (mode elevation, vault payments, task
     assignment)
  7. Personality and adapter management (auto-gen system prompts,
     Claude adapter pooling)
  8. Status and diagnostics (get_status, health checks, compaction,
     idle cleanup)
  9. Hierarchy and escalation (parent lookups, permission checks,
     error routing)

## Proposed split

Four extracted modules + one thinned core. LOC estimates are
load-bearing for the staging plan but should be re-measured during
design:

| Module | LOC | Responsibility |
|---|---|---|
| `approval_handler.py` | ~400 | Mode elevation, vault payments, task failure handling, pending-request expiry |
| `lifecycle_manager.py` | ~600 | Entity register/spawn/kill/stop, hierarchy restore, max-sessions enforcement |
| `message_dispatcher.py` | ~700 | `send_to_entity`, parse + route actions, parse-error feedback, escalation |
| `wake_scheduler.py` | ~250 | Wake-on-inbound, rate limiting, detached-kickoff tasks |
| `state_manager.py` (core retained) | ~400 | `__init__`, persist, audit, entity/session/adapter dicts, status/health |

Seam quality: clean dependency graph. Most splits need only the
central `state_manager` plus shared stores. `message_dispatcher` will
need a callback into `lifecycle_manager` for spawn actions — define
that as a narrow interface, not full cross-import.

## Coupling

**Imports TO `manager.py`** (8 sites):

- `__main__.py` (central orchestrator)
- `telegram/bridge.py`, `web/app.py`, `cli/local.py` (entry points)
- `web/view_model.py`, `commands/dispatch.py` (API handlers)
- `process/scheduler.py` (priority scheduling)

**Key dependencies FROM `manager.py`:**

- `Router` (17 uses) — message queue persistence
- Stores — `audit_log` (50×), `vault_store` (10×),
  `mode_request_store` (4×), `entity_store` (2×), `task_store` (2×)
- `NotificationDispatcher` (15×) — alerts to user
- Entity models (`Maestro`, `TeamLead`, `Worker`, `Vault`)
- Claude adapter (4 pooling ops) — subprocess lifecycle
- `WorktreeManager` — filesystem isolation
- `QuotaMonitor` — plan-quota health

**Circular import risk:** low. Only 3 late imports exist (lines
~1090–1091) for type-checking. Candidates for extraction
(`approval_handler`, `wake_scheduler`) have zero circular dependencies
with anything outside `process/`.

## Precedent — QuotaMonitor split (commit `30fa909`)

Pattern applied successfully on a smaller scale:

- `quota_state.py` — pure state machine (debounce for blind/recovered,
  nullable `resets_at` handling).
- `quota_alerts.py` — pure text/formatting (alert messages, usage
  templates).
- `quota_monitor.py` — orchestrator (polling loop, alert dispatch,
  persistence).

Lessons to apply for `manager.py`:

1. Extract **pure logic first** — approval caps, rate-limit windows,
   parse-failure debounce. Reusable state classes.
2. Extract **text/formatting** — notification bodies, error messages.
   Keeps presentation testable.
3. Keep **orchestration** in the core — it decides *when* to invoke
   extracted logic, not *how*.
4. Test extracted modules independently of async/store infrastructure.

## Red flags / surprises

| Flag | Severity | Notes |
|---|---|---|
| Non-reentrant `asyncio.Lock` | Medium | `_state_lock` guards `_entities/_sessions` mutations (~lines 221–227). **Do not await across this lock in extracted modules.** Critical sections only. _Note (Ticket 007):_ `_sessions` is the headless-era dict — always empty under PTY (only the now-removed `spawn_entity` wrote it). 007 deletes `spawn_entity`/`_sessions` and re-points `active_count`/`get_status`/`health_check` onto `_adapters`, so the lock then guards `_entities` only. |
| Detached task tracking | Medium | Kickoff tasks (`_kickoff_tasks`, ~line 1074) and wake tasks (`_wake_tasks`, ~line 1276) are fire-and-forget but tracked to prevent GC. Splits must preserve the tracking. |
| Mutable shared state in `_handle_actions` | Low-Medium | Lines 822–829 reset `_last_*` lists on every dispatch. Tests rely on this for assertions. Extracts must expose equivalent introspection. |
| Parse-failure debounce dict | Low | Lines 244–248 — `_parse_failure_budget` deque per entity. Must remain central, or pass as context to extracts. |
| Test coverage | Low | 40+ tests in `test_process_manager.py` suggest healthy coverage. Approval and vault cap paths are well-tested. |

Threading model: relies on CPython GIL for single-key dict reads
(unlocked). Writes protected by `asyncio.Lock` only during
register/unregister. Acceptable; preserve the pattern.

## Recommended staging (design-stage input)

1. Extract `approval_handler.py` first — loosest coupling, highest
   test coverage, no async complexity.
2. Extract `wake_scheduler.py` next — self-contained state + scheduling.
3. Extract `message_dispatcher.py` — depends only on router + entities
   dict; needs the lifecycle callback.
4. Extract `lifecycle_manager.py` — heaviest, but testable with mock
   stores.
5. Thin the core into `state_manager.py`.

Each step lands as its own PR. Tests stay green between steps.
