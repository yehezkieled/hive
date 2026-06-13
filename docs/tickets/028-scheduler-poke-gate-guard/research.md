# Research — Ticket 028

Findings with file refs. Answers `questions.md` in order.

## 1–2. The injection path, and why it isn't scheduler-specific

The scheduler poke becomes raw keystrokes through one shared funnel:

```
PriorityScheduler.run_once()            scheduler.py:219-227
  └─ pm.send_to_entity(m.name, facts)   scheduler.py:222
       └─ MessageDispatcher.send_to_entity()   message_dispatcher.py:86
            └─ adapter.send_turn(prompt)       message_dispatcher.py:202
                 └─ writes prompt + Enter into the PTY stdin
```

`send_to_entity` is the **single outbound chokepoint**: the facade delegates
to it (`manager.py:318-319`), and every "type at an entity" caller routes
through it — the scheduler (`scheduler.py:222`, `:237`), wake-on-inbound and
peer delivery (`wake` / `router` paths that call `send_to_entity`), and the
manual `/eval`. So the menu-submission hazard is a property of **typing into a
parked PTY from anywhere**, not of the scheduler. → guarding the chokepoint
(Option B) covers all callers; guarding only the scheduler (Option A) leaves
the others exposed.

## 3. All three gate kinds park the PTY — not just AskUserQuestion

`GateKind = Literal["plan", "ask", "permission"]` (`gates.py:24`). The PTY
reader routes **every** detected `Gated` event through the same handler:

```
PtySession._handle_gate()               pty_session.py:380-393
  └─ gate_coordinator.resolve(...)      pty_session.py:388
```

So a maestro can be parked on a plan-approval menu (`ExitPlanMode`) or a
permission menu just as easily as an AskUserQuestion menu — and the trailing
Enter submits *their* default too. Banning AskUserQuestion would close one of
three kinds; the guard must be **gate-kind-agnostic**, which `pending_request_id`
naturally is.

## 4. The authoritative signal: `gate_coordinator.pending_request_id`

`GateCoordinator.pending_request_id(entity_name) -> int | None`
(`gate_coordinator.py:163-165`). Lifecycle:

- **Set** the instant a Turn parks: `resolve()` registers the doorbell and
  `self._pending[entity_name] = request_id` (`gate_coordinator.py:104-106`).
- **Torn down** on resume in the `finally` (`gate_coordinator.py:111-114`), so
  it is non-None *exactly while* a Turn is parked on a gate.
- Set for **all** gate kinds, because `_handle_gate` (above) calls `resolve`
  for every kind.

A redundant secondary signal exists: `entity.state == EntityState.GATED`
(`entity.py:23`, set via the same `_handle_gate` → `_set_gate_state` →
`approval_handler` flow). `pending_request_id` is preferred as the
coordinator-owned source of truth.

## 5. The signal IS set in the 029 failure scenario

Key risk checked. Ticket 028's own write-up notes the coordinator "is correctly
designed to park forever … this bug **bypasses** it entirely by writing to the
raw TUI beneath it" (`gate_coordinator.py:11-12`, issue #25). That means
`resolve()` *was* called and the doorbell *was* registered — i.e.
`pending_request_id` **was non-None** during the otter incident. Ticket 029's
"un-bridged" gap is that the *user-facing notification* never reached the user,
not that the coordinator stopped tracking. → `pending_request_id` is reliable
for this exact bug.

## 6. `waitingFor` fallback — not needed for v1

The only scenario where `pending_request_id` is None yet the PTY is still on a
menu is a gate the **detector entirely misses** (so Hive never parks). That is a
separate defect (a `GateDetector` gap), and the reader would mis-accept the turn
regardless. Claude Code's own session-state file
`~/.claude/sessions/<pid>.json` (already read for session pinning,
`pty_session.py:43-54`, `transcript_reader.py:73`) carries a `waitingFor` field
that could catch it. Deferred: it adds a file read per poke for a case outside
this bug's scope. Noted as future hardening in `design.md`.

## 7. Guarding `send_to_entity` does NOT block gate answers

Gate resolution never flows through `send_to_entity`. It flows:

```
/approve|/deny gate <id>
  └─ approval_handler.approve_gate/deny_gate   approval_handler.py:498-541
       └─ gate_coordinator.ring(requester)     approval_handler.py:519-520, 539-540
            └─ doorbell.set() → parked resolve() returns keys
                 └─ PtySession._inject_keys(...)   pty_session.py:391-393
```

A separate, menu-aware path that injects precise navigation keystrokes. The
chokepoint guard only blocks *new-turn* text, never the answer. The original
parked `send_to_entity` call (already past the guard) resumes normally when the
doorbell rings.

## 8. No message loss

- **Peer messages** are drained at turn **start** (`message_dispatcher.py:108-116`),
  not at park time. Placing the guard *before* the drain leaves queued peer
  messages untouched; the parked turn's tail `schedule_wake_if_pending`
  (`message_dispatcher.py:254`) re-delivers them after the gate resolves.
- **Facts poke** is regenerated fresh each tick — dropping it is correct
  (stale facts should not queue).
- **User free text** has no valid landing spot while a menu is open (the PTY is
  in menu mode), so the guard returns a short notice instead of silently
  dropping or mis-typing it.

## 9. Existing pattern to match

`pending_request_id` is already consumed with the None-guard idiom:

- `approval_handler.py:466-469` — `_notify_gate_waiting`.
- `approval_handler.py:568-570` — `reconcile_orphaned_gates` (skips entities
  with a live doorbell).

The guard should reuse `gate_coordinator is not None and
gate_coordinator.pending_request_id(name) is not None`.

## 10. Physical placement in `send_to_entity`

After the `entity is None` KeyError check (`message_dispatcher.py:101-103`) and
**before** `entity.last_activity_at = datetime.now(UTC)` (`:106`) and the inbox
drain (`:108-116`). Guarding first avoids touching activity state or draining
the router for a send we are about to refuse.

## 11. Test scaffolding

- **Scheduler**: `tests/test_scheduler.py` — `run_once` tests rebind
  `manager.send_to_entity` to a fake/`AsyncMock` and assert the poked list
  (`:167-210`). A parked-maestro test fits the same shape.
- **Dispatcher**: `tests/process/test_message_dispatcher.py` — drives the real
  `send_to_entity` through a `StubManager` + `FakeTurnAdapter` (`:88-253`).
  **Gap found:** `StubManager` has no `gate_coordinator` attribute
  (`__init__`, `:140-175`). The build must give it one (`= None`) or a stub
  `is_parked_at_gate`, or the new guard's attribute access breaks every existing
  full-turn test. Cheap, but must be in the plan.
