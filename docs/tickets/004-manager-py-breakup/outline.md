# Outline — Ticket 004: break up `process/manager.py`

Implementation structure. The shape every slice follows, then the
per-slice breakdown. Read `design.md` first — this is the *how it's
arranged*, not the *why*.

## The collaborator template (all four modules)

Every extracted module is the same shape — a class taking the manager
as its only constructor arg, reaching shared state through it:

```python
# src/hive/process/<module>.py
from __future__ import annotations
from typing import TYPE_CHECKING

# ... module-level symbols that moved here live at top (see design §map)

if TYPE_CHECKING:
    from hive.process.manager import ProcessManager  # type hint only, no runtime import


class <Collaborator>:
    """One responsibility cluster lifted out of ProcessManager."""

    def __init__(self, mgr: "ProcessManager") -> None:
        self._mgr = mgr

    async def <moved_method>(self, ...):
        # body copied verbatim, but every self._X that is shared state
        # or a sibling method becomes self._mgr._X / self._mgr.<method>
        entity = self._mgr._entities.get(name)
        async with self._mgr._state_lock:      # the ONE lock, reached via facade
            ...
        await self._mgr._persist(entity)        # core helper, via facade
        return await self._mgr.send_to_entity(...)  # cross-module call, via facade
```

## The facade shape (`manager.py` after the split)

```python
class ProcessManager:
    def __init__(self, ...):
        # 1. construct ALL shared state (unchanged): dicts, the single
        #    _state_lock, stores, _last_* lists, _kickoff_tasks,
        #    _wake_tasks, _parse_failure_budget, _wake_budget
        ...
        # 2. wire collaborators (each gets the back-ref)
        self.lifecycle = LifecycleManager(self)
        self.dispatcher = MessageDispatcher(self)
        self.wake = WakeScheduler(self)
        self.approvals = ApprovalHandler(self)

    # 3. retained core methods stay as real implementations:
    #    _persist, _audit, _notify, _record_usage, _peer_directory_for,
    #    _parent_of, entities, active_count, get_status, health_check,
    #    restore, rebuild_hierarchy

    # 4. thin delegations for everything external code/tests bind to —
    #    PUBLIC and externally-referenced PRIVATE methods:
    async def spawn_worker(self, *a, **k):
        return await self.lifecycle.spawn_worker(*a, **k)
    def _on_gate_state(self, *a, **k):                 # private, but __main__ + tests bind it
        return self.approvals._on_gate_state(*a, **k)
    async def _gate_nudge(self, *a, **k):              # private, __main__ on_nudge=...
        return await self.approvals._gate_nudge(*a, **k)
    # ... (full delegation list in design §facade rule)

# 5. re-export module symbols tests import, to preserve import paths:
from hive.process.lifecycle_manager import (
    LifecycleManager, _render_auto_personality, _adapter_config_from_entity,
)
from hive.process.message_dispatcher import MessageDispatcher
from hive.process.wake_scheduler import WakeScheduler, _WAKE_ON_INBOUND_TEXT
from hive.process.approval_handler import ApprovalHandler
```

> Mechanics note: the four delegations and the `__init__` wiring for
> *all* collaborators can be added in slice 1 with `pass`-through
> stubs, or each slice can add its own wiring + delegations as it lands.
> Recommended: each slice adds **only its own** collaborator + its own
> delegations, so each PR is self-contained and the suite stays green.

## Per-slice breakdown

Each slice = one PR. New module + its test file + the facade
delegations for the methods it moved. Suite green at every step.

### Slice 1 — `approval_handler.py` (pattern-setter)

- **Move:** `_approver_for`, `request_mode_change`, `request_payment`,
  `approve_vault_action`, `deny_vault_action`, `approve_mode_request`,
  `deny_mode_request`, `_on_gate_state`, `_notify_gate_waiting`,
  `_gate_nudge`, `approve_gate`, `deny_gate`, `reconcile_orphaned_gates`,
  `expire_old_mode_requests`, `_escalation_target_for`,
  `handle_task_failure` → `ApprovalHandler(self._mgr)`.
- **Facade delegations:** all the public ones above **plus private**
  `_on_gate_state`, `_gate_nudge` (wired by `__main__`/adapter + asserted
  by tests). This slice proves the private-method facade rule.
- **New tests:** `tests/process/test_approval_handler.py` — mode/vault
  cap paths, gate approve/deny, stale-gate reconcile, task-failure
  escalation, against a stub manager.
- **Note:** `handle_task_failure` calls `_escalation_target_for`
  (intra-module) and `send_to_entity` (`self._mgr.send_to_entity`, still
  on core at this point — works via facade).

### Slice 2 — `wake_scheduler.py`

- **Move:** `enable_wake_on_inbound`, `_on_inbound_wake`,
  `_wake_entity`, `_auto_kickoff` → `WakeScheduler(self._mgr)`.
- **Module symbols moved here:** `_WAKE_ON_INBOUND_TEXT` (re-exported
  from `manager.py`), `_SPAWN_KICKOFF_TEXT`,
  `_WAKE_BUDGET_WINDOW_SECONDS`, `_WAKE_BUDGET_MAX_PER_WINDOW`.
- **Facade delegations:** `enable_wake_on_inbound` (public, `__main__`),
  `_auto_kickoff` (private — scheduled by the dispatcher as
  `self._mgr._auto_kickoff`).
- **Hazards:** `_wake_tasks` and `_wake_budget` stay facade-owned;
  mutate via `self._mgr._wake_tasks.add(...)` / `self._mgr._wake_budget`.
- **New tests:** `tests/process/test_wake_scheduler.py` — rate-limit
  window (the `_wake_budget` deque), wake task scheduling + GC-tracking.

### Slice 3 — `message_dispatcher.py`

- **Move:** `send_to_entity`, `_handle_actions`, `_handle_parse_errors`,
  `_task_id_for` → `MessageDispatcher(self._mgr)`.
- **Module symbols moved here:** `_PARSE_FAILURE_WINDOW_SECONDS`,
  `_PARSE_FAILURE_MAX_PER_WINDOW`.
- **Facade delegations:** `send_to_entity` (public, called everywhere)
  **plus private** `_handle_actions` (asserted by tests).
- **⚠ The fragile seam:** `_handle_actions` resets the `_last_*` lists
  by **rebinding** (`self._last_x = []`). In the collaborator this MUST
  be `self._mgr._last_x = []`. A local rebind silently breaks every
  `_last_*` test assertion. Call this out in the PR description and the
  review checklist.
- **Cross-module (via facade):** `request_mode_change`,
  `request_payment`, `handle_task_failure` (→ approvals, moved in
  slice 1); `create_team`, `spawn_worker`, `kill_entity` (→ lifecycle,
  still on core until slice 4 — facade makes this transparent);
  `_auto_kickoff` scheduled + tracked in `self._mgr._kickoff_tasks`.
- **New tests:** `tests/process/test_message_dispatcher.py` — action
  routing, parse-error debounce window, `_last_*` population.

### Slice 4 — `lifecycle_manager.py` (heaviest)

- **Move:** `_personality_path`, `_maybe_write_auto_personality`,
  `_maybe_delete_auto_personality`, `_preempt_for_priority`,
  `register_maestro`, `register_entity`, `spawn_entity`,
  `_get_or_create_adapter`, `create_team`, `spawn_worker`, `kill_team`,
  `kill_entity`, `kill_all`, `stop_all`, `compact_entity`,
  `kill_idle_entities` → `LifecycleManager(self._mgr)`.
- **Module symbols moved here:** `_render_auto_personality`
  (re-exported), `_adapter_config_from_entity` (re-exported) — its new
  home imports `ADVISOR_ENABLED` + `ClaudeAdapterConfig` itself.
- **Facade delegations:** all the public register/spawn/kill/compact
  methods **plus private** `_get_or_create_adapter` (test_advisor_mcp).
- **⚠ Lock-heavy:** all nine `async with self._state_lock` critical
  sections live here. Copy each `with`-block verbatim into
  `self._mgr._state_lock`; never add an `await` inside one.
  `spawn_entity`'s atomic entity+session insert and `kill_entity`'s
  pop blocks are load-bearing.
- **New tests:** `tests/process/test_lifecycle_manager.py` — spawn/kill
  lifecycle, hierarchy restore, max-sessions preemption, with mock
  stores.

### Slice 5 — thin core + smoke

- `manager.py` now contains only: shared-state construction, the
  retained core helpers, collaborator wiring, the delegation block, and
  re-exports. Target ≈400 LOC.
- **Cleanup:** remove any now-dead imports; confirm no orphaned
  helpers; verify the LOC target.
- **Smoke (acceptance-mandated, not suite-only):** a maestro turn
  completes end-to-end on the refactored code — exercise both the
  Telegram bridge and the web path. Smoke from the Tailscale IP, and
  for the web path use an actual browser check, not just curl.

## Validation gate (every slice)

```
ruff check src/ tests/ && ruff format --check src/ tests/ \
  && pytest -m "not integration"
```

Plus: the moved code is a verbatim copy with only `self.` →
`self._mgr.` rewrites on shared state and sibling calls — no logic
edits. Diff review confirms behaviour preservation.

## Test layout

New tests go under `tests/process/` (mirrors `src/hive/process/`),
matching the existing `tests/process/test_auto_retrieve.py`. The big
existing suite `tests/test_process_manager.py` keeps passing unmodified
throughout — it exercises the facade, which preserves every method and
symbol.
