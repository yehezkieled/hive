# Design — Ticket 028

## Decision

Refuse to inject into the PTY of an entity that is **parked at an interactive
gate**, at the shared outbound chokepoint, keyed on the coordinator's in-memory
pending-gate signal.

- **Primary guard — `MessageDispatcher.send_to_entity`** (the one funnel every
  sender flows through). If the target is parked at a gate, return a short
  notice **without** draining the inbox or calling `adapter.send_turn`.
- **Secondary skip — `PriorityScheduler.run_once` / `run_once_for`.** Skip a
  parked maestro before building its (DB-backed) facts prompt — a clean,
  scheduler-specific log line and avoids needless `task_store`/`token_store`
  queries. The chokepoint remains the load-bearing guard.
- **Shared helper — `ProcessManager.is_parked_at_gate(name) -> bool`.** One
  place for the None-guard and the future `waitingFor` fallback; both call sites
  use it.

Signal:

```python
def is_parked_at_gate(self, entity_name: str) -> bool:
    gc = self.gate_coordinator
    return gc is not None and gc.pending_request_id(entity_name) is not None
```

## Why this shape

```
   scheduler ─┐                         the bug is "raw text typed into a PTY
   peer msg ──┤                          parked on a menu" — a property of the
   your TG ───┼─▶ send_to_entity  ◀───── PTY, not of any one caller. Guard the
   /eval ─────┘   (guard here)           junction → all callers covered once.
```

While parked, the PTY is in **menu mode**: it accepts navigation keystrokes
(↑/↓/Enter) that select an option, not free text as a message. So a new-turn
prompt has *no valid landing spot*. The only correct move is to not type it and
wait until the gate resolves and the entity returns to a normal prompt. Gate
*answers* take a different, menu-aware path (`ring → resolve → _inject_keys`),
so the guard never blocks a legitimate decision (research §7).

## Behaviour when parked

| Sender | What the guard does | Why no loss |
|--------|--------------------|-------------|
| Scheduler poke | skip before building facts | regenerated next tick |
| Peer message | refuse; **leave inbox undrained** | re-delivered post-resolve via turn-end `schedule_wake_if_pending` (research §8) |
| User free text | refuse; return a notice with `/approve gate <id>` guidance | no valid landing spot in menu mode; notice tells the user what to do |
| `/eval` | refuse; return notice | manual, idempotent |

The chokepoint guard sits **after** the `entity is None` check and **before**
`last_activity` update + inbox drain (research §10). Return value is a sentinel
string (the method returns `str`), e.g.
`"<{name} is parked at gate {id}; answer it with /approve gate {id} or /deny gate {id}>"`.
Programmatic callers ignore the return; the user path surfaces it.

## Alternatives considered

| Option | Verdict | Reason |
|--------|---------|--------|
| **A — guard scheduler only** | rejected | Closes the scheduler hole only; peer / user / `/eval` can still submit a parked menu's default (research §1–2). |
| **Ban maestro AskUserQuestion, ask via async message** | rejected for 028 | Covers only the `ask` gate kind, not plan/permission (research §3); a prompt instruction isn't enforcement; the async maestro→user path is itself unshipped (Ticket 021). Captured as input to 029/021, not a substitute for the guard. |
| **Key on `entity.state == GATED`** | viable secondary | Same lifecycle, but `pending_request_id` is the coordinator-owned source of truth and matches existing call sites (research §9). |
| **`waitingFor` session-state fallback** | deferred | Only helps for detector-missed gates (out of this bug's scope); adds a file read per poke. Future hardening — the helper is the seam to add it. |

## Side effects

- **CONTEXT.md** — no new term. "Interactive gate", "GateCoordinator",
  "Session pinning" already cover the vocabulary.
- **ADR** — none. This extends the gate bridge of **ADR 0004** with a safety
  invariant ("no new-turn injection while a gate is parked; gate answers are the
  only valid input"); it reverses nothing and is bugfix-level. Recorded here,
  not as a new ADR.

## Acceptance mapping

- *Scheduler/any sender skips a parked entity* → chokepoint guard + scheduler
  skip via `is_parked_at_gate`.
- *No code path submits a gate default as a side-effect* → guard at the single
  chokepoint all `send_to_entity` callers share.
- *Test: poke at a parked entity resolves nothing and never reaches the PTY* →
  dispatcher test asserts `send_turn` not called + gate row untouched; scheduler
  test asserts the parked maestro is skipped.
