# Design — Ticket 004: break up `process/manager.py`

Chosen approach for splitting the `ProcessManager` god object into a
thin core plus four focused modules, **zero behaviour change**.

Supersedes the size figures in `research.md` (a dated 2026-05-30
snapshot). Re-measured at design stage against the worktree:

| Metric | research.md (05-30) | now |
|---|---|---|
| LOC | 2,308 | **2,469** |
| Methods on `ProcessManager` | 47 | **53** |
| Interactive-gate methods | 0 (postdates research) | **6** (Ticket 003) |

The boundary in this document was verified method-by-method against
the live code (53 methods + module-level symbols traced; see
§Boundary verification). The partition is complete and disjoint; the
adjustments below are what the verification surfaced.

## Pattern — composition (facade + collaborators)

`ProcessManager` stays in `manager.py` as a **facade and shared-state
holder**. The four extracted modules are **collaborator objects**,
each instantiated in `__init__` holding a back-reference (`self._mgr`)
to the manager. Public methods on `ProcessManager` become thin
delegations.

```
   external callers (__main__, telegram/bridge, web/app, cli/local,
   commands/dispatch, process/scheduler, web/view_model, 15+ test files)
                                │
                  from hive.process.manager import ProcessManager
                                ▼
        ┌──────────────────────────────────────────────────┐
        │  manager.py — ProcessManager  (FACADE + STATE)     │
        │  owns: _entities, _sessions, _adapters,            │
        │        _state_lock (single asyncio.Lock),          │
        │        all stores, _last_* lists, _kickoff_tasks,  │
        │        _wake_tasks, _parse_failure_budget,         │
        │        _wake_budget; _persist/_audit/_notify       │
        │  public + externally-referenced methods delegate ▼ │
        └───┬──────────┬───────────┬───────────┬─────────────┘
            │ self._mgr│ self._mgr │ self._mgr │ self._mgr
            ▼          ▼           ▼           ▼
      Lifecycle    Message     Wake        Approval
      Manager      Dispatcher  Scheduler   Handler
   (register/spawn (send +    (wake-on-   (mode/vault/
    /kill/compact)  actions)   inbound +    gate/task-fail)
                               kickoff)
```

Why composition and not mixins or pure functions: see
[ADR 0006](../../adr/0006-god-object-breakup-composition.md). Short
version — mixins keep one class so `self._entities` works untouched
(small diff), but a mixin can't be instantiated or unit-tested without
the whole `ProcessManager` state surface, which fails the acceptance
criterion *"each new module has its own test file with isolated unit
tests."* Composition costs a bigger diff (every `self._foo` in a moved
method becomes `self._mgr._foo`) but each collaborator can be tested
against a stub manager. Pure functions (the QuotaMonitor precedent)
only fit stateless logic; ~1,900 of these LOC are stateful
orchestration. Where genuinely-pure logic sits inside a collaborator
(rate-limit windows, parse-failure debounce), extract it as a small
pure helper — the pure pattern nested inside composition.

## Core naming — stays `manager.py`

The thinned core keeps the file name `manager.py` and the class name
`ProcessManager`. `research.md` proposed renaming it to
`state_manager.py`; that is **dropped** because 9 source sites plus 15
test files import `from hive.process.manager import ProcessManager`,
and acceptance demands zero API breakage. Renaming the file would
break every one of them.

## Module map (verified)

The retained core owns all shared state plus the cross-cutting helpers
every collaborator calls.

**`manager.py` — core / facade** (`__init__`, `_persist`, `_audit`,
`_peer_directory_for`, `_record_usage`, `entities`, `active_count`,
`get_status`, `health_check`, `_notify`, `restore`,
`rebuild_hierarchy`, `_parent_of`). Holds every piece of shared
state.

| Module | Methods | Module-level symbols it owns |
|---|---|---|
| `lifecycle_manager.py` | `_personality_path`, `_maybe_write_auto_personality`, `_maybe_delete_auto_personality`, `_preempt_for_priority`, `register_maestro`, `register_entity`, `spawn_entity`, `_get_or_create_adapter`, `create_team`, `spawn_worker`, `kill_team`, `kill_entity`, `kill_all`, `stop_all`, `compact_entity`, `kill_idle_entities` | `_render_auto_personality`, `_adapter_config_from_entity` |
| `message_dispatcher.py` | `send_to_entity`, `_handle_actions`, `_handle_parse_errors`, `_task_id_for` | `_PARSE_FAILURE_WINDOW_SECONDS`, `_PARSE_FAILURE_MAX_PER_WINDOW` |
| `wake_scheduler.py` | `enable_wake_on_inbound`, `_on_inbound_wake`, `_wake_entity`, `_auto_kickoff` | `_WAKE_ON_INBOUND_TEXT`, `_SPAWN_KICKOFF_TEXT`, `_WAKE_BUDGET_WINDOW_SECONDS`, `_WAKE_BUDGET_MAX_PER_WINDOW` |
| `approval_handler.py` | `_approver_for`, `request_mode_change`, `request_payment`, `approve_vault_action`, `deny_vault_action`, `approve_mode_request`, `deny_mode_request`, `_on_gate_state`, `_notify_gate_waiting`, `_gate_nudge`, `approve_gate`, `deny_gate`, `reconcile_orphaned_gates`, `expire_old_mode_requests`, `_escalation_target_for`, `handle_task_failure` | — |

Two assignments differ from the obvious topical grouping, confirmed by
tracing actual callers:

- **`_task_id_for` → `message_dispatcher`** (not `approval_handler`).
  Its only caller in the whole file is `_handle_actions`;
  `handle_task_failure` does *not* call it. Grouping it with the
  dispatcher turns a cross-module hop into an intra-module call.
- **`_parent_of` and `_peer_directory_for` stay in core.** Both are
  stateless/read-only helpers consumed by more than one collaborator
  (or by tests directly). A shared core helper reached via
  `self._mgr._parent_of` is correct; moving either into one consumer
  would force the other into a cross-module hop. `_peer_directory_for`
  touches only `_entities` (core-owned), so it is harmless in core.

## Import safety — re-export, no cycle

`manager.py` already has `from __future__ import annotations` (line 3),
so all type annotations are strings and never evaluated at import.
Collaborators type-hint `self._mgr: ProcessManager` under
`if TYPE_CHECKING:` and import **nothing** from `manager.py` at module
load. Dependency direction is one-way: `manager.py` imports the four
collaborator classes (to instantiate them) and re-exports their
module-level symbols. No cycle.

Three symbols are imported directly by tests and **must stay
importable from `hive.process.manager`**:

- `_render_auto_personality` (`test_auto_retrieve.py:374`) — impl
  moves to `lifecycle_manager.py`, re-exported from `manager.py`.
- `_WAKE_ON_INBOUND_TEXT` (`test_process_manager.py:1795`) — impl
  moves to `wake_scheduler.py`, re-exported.
- `ProcessManager` (15 test files + 9 source sites) — stays put.

Re-export line in `manager.py`, e.g.:
`from hive.process.lifecycle_manager import (LifecycleManager,
_render_auto_personality, _adapter_config_from_entity)`. **Caution:**
`_adapter_config_from_entity` references module-level `ADVISOR_ENABLED`
and `ClaudeAdapterConfig` — its new home must import those itself, not
reach back into `manager.py`.

## The facade rule — delegate private methods too

This is the correction the verification forced. The naive rule —
"public methods become thin delegations" — is **insufficient**.
External wiring and ~40 test call sites bind to *private* methods on
the manager instance:

- `__main__.py` wires `on_nudge=process_manager._gate_nudge`, passes
  `self._on_gate_state` into the adapter, and calls
  `process_manager._persist`, `.enable_wake_on_inbound`, `.restore`,
  `.rebuild_hierarchy`, `.reconcile_orphaned_gates`.
- Tests patch/call `pm._record_usage`, `pm._peer_directory_for`,
  `pm._on_gate_state`, `pm._get_or_create_adapter`, `pm._handle_actions`.

**Rule:** every method any external module or test references on the
`ProcessManager` instance stays a bound attribute on the facade —
public *or* private. Private methods that **move out** to a
collaborator but are externally referenced need an explicit thin
delegation on the facade: `_handle_actions`, `_get_or_create_adapter`,
`_on_gate_state`, `_gate_nudge`, `_auto_kickoff`. Private methods that
**stay in core** (`_persist`, `_record_usage`, `_audit`, `_notify`,
`_peer_directory_for`, `_parent_of`) are naturally still bound — no
delegation needed. A monkeypatch like `pm._record_usage = AsyncMock()`
must keep working, so delegations must be real bound methods, not
descriptors.

Cross-collaborator calls route **through the facade**, never
collaborator-to-collaborator: e.g. the dispatcher calls
`self._mgr.spawn_worker(...)`, not `self._mgr.lifecycle.spawn_worker`.
This keeps each collaborator dependent only on `ProcessManager`'s
surface — the same contract external callers use — so no collaborator
imports another. There are two-way couplings
(`message_dispatcher ↔ lifecycle_manager` via
`send_to_entity ↔ compact_entity`; `message_dispatcher ↔ wake_scheduler`
via kickoff scheduling); both directions go through the facade, so
they are acceptable under composition.

## Hazard preservation

Verified against the live code. All shared state stays facade-owned;
collaborators mutate it through `self._mgr`.

| Hazard | Status | Rule for the implementer |
|---|---|---|
| Single non-reentrant `_state_lock` | ✅ safe | One `asyncio.Lock()` on the facade; collaborators use `async with self._mgr._state_lock`. Never create a second lock. |
| No `await` inside a lock critical section | ✅ safe | Verified: every `async with self._state_lock` guards only sync dict mutations; all awaits sit outside. Copy the `with`-blocks verbatim — it's a code move, not a logic change. `spawn_entity`'s atomic entity+session insert and `kill_entity`'s pop blocks are load-bearing. |
| **`_last_*` lists are REBOUND, not cleared** | ⚠ **fragile** | `_handle_actions` does `self._last_routed_actions = []` (rebind), not `.clear()`. In the dispatcher this **must** be `self._mgr._last_routed_actions = []` — assign through the back-ref. A local rebind leaves the facade attribute tests read stale and silently breaks every `_last_*` assertion. The single most fragile seam in the split. |
| `_kickoff_tasks` / `_wake_tasks` GC-tracking | ✅ safe | Sets stay facade-owned. Dispatcher schedules via `self._mgr._kickoff_tasks.add(task)` + `add_done_callback(self._mgr._kickoff_tasks.discard)`. Mutate the facade-held set, never a local. |
| `_parse_failure_budget` deque (dispatcher) | ✅ safe | `defaultdict(deque)` stays facade-owned; `_handle_parse_errors` reads/mutates `self._mgr._parse_failure_budget[name]`. |
| `_wake_budget` deque (wake) | ✅ safe | Sibling of `_parse_failure_budget`; the design inventory originally omitted it. Stays facade-owned; `_on_inbound_wake` reaches it via `self._mgr._wake_budget`. |
| `_auto_kickoff` timing | ✅ preserve | `_handle_actions` schedules `_auto_kickoff` **detached** so its recursive `send_to_entity` runs *after* `_handle_actions` returns and does not reset the parent's `_last_*` lists. Preserve this ordering across the dispatcher↔wake boundary. |

**Pre-existing latent issue (not introduced by this split):**
`_on_gate_state` (line 2020) does
`asyncio.create_task(self._notify_gate_waiting(...))` with no set
tracking — the task can be GC'd mid-flight. The split preserves
existing behaviour. Noted for the implementer; fixing it (tracking the
task) is behaviour-adjacent and out of scope for this zero-change
refactor.

## Sequencing — serial 5-PR chain

Normally a fan-out ticket runs slices in parallel. This one is serial,
for two compounding reasons:

1. **File-overlap.** All five slices rewrite the *same* file
   (`manager.py`) — each cuts a chunk out and re-thins the core.
   Concurrent agents would conflict nonstop.
2. **Pattern dependency.** Slice 1 establishes the composition
   contract (collaborator `__init__(self, mgr)`, the `self._mgr`
   back-ref, facade delegation including private methods). Slices 2–5
   copy it; they can't follow a pattern that isn't written yet.

Order — safest / pattern-setting first, riskiest later, verify last:

1. `approval_handler` — loosest coupling, no `_state_lock` mutations,
   highest existing test coverage. Proves the pattern (incl. the
   private-method facade rule, exercised by `_on_gate_state` /
   `_gate_nudge`).
2. `wake_scheduler` — self-contained; owns the `_wake_tasks` and
   `_wake_budget` hazards.
3. `message_dispatcher` — `send_to_entity` + the 287-LOC
   `_handle_actions`; carries the `_last_*` rebind seam and
   `_parse_failure_budget`.
4. `lifecycle_manager` — heaviest, and where all nine `_state_lock`
   critical sections live. Goes after the pattern is battle-tested.
5. **Thin core + smoke** — `manager.py` is now facade + state holder
   (~400 LOC target). Final cleanup + the mandated end-to-end
   maestro-turn smoke (Telegram + web).

The facade makes the order safe regardless of remaining dependencies:
a method calling `self._mgr.send_to_entity(...)` doesn't care whether
`send_to_entity` is still on the core or already moved — the call site
never changes.

## Boundary verification

The 53-method partition was traced against the live code by a
multi-agent pass (one agent per module bucket + an adversarial boundary
checker). Findings, all folded into this design:

- Partition complete and disjoint (53 methods + 3 named symbols
  assigned exactly once); no unassigned or double-assigned methods.
- No circular-import risk (one-way import direction + existing
  postponed annotations).
- Every cross-module call is facade-routable.
- All concurrency hazards preserved under composition; the `_last_*`
  rebind is the one fragile seam (flagged above).
- Completeness gaps fixed: 5 module constants + the `_wake_budget`
  deque were missing from the original inventory and are now assigned.

## Alternatives considered

- **Mixins** — rejected: cosmetic split, can't unit-test a module in
  isolation. See ADR 0006.
- **Pure functions only** (QuotaMonitor pattern) — rejected: only fits
  stateless logic; most of `manager.py` is stateful orchestration.
- **Rename core to `state_manager.py`** — rejected: breaks 24 import
  sites; acceptance demands zero API breakage.
- **Gates as a 5th module (`gate_handler.py`)** — rejected: gates
  share `mode_request_store` with mode-change approvals (a gate row
  *is* a `kind='gate'` row); splitting fragments one responsibility.
  Folded into `approval_handler`, matching the ticket's four-module
  acceptance.

## Cross-cutting impact

- **New ADR:** `docs/adr/0006-god-object-breakup-composition.md` —
  records the composition-over-mixins decision as the house pattern
  for the Phase 2 restructure arc (Vault consolidation #005 will face
  the same fork).
- **CONTEXT.md:** no change. Pure code-structure refactor; no new or
  changed domain terms (CONTEXT.md is a glossary, free of
  implementation detail).
- **README / ARCHITECTURE:** no change — the public surface
  (`hive.process.manager.ProcessManager`) is preserved.
