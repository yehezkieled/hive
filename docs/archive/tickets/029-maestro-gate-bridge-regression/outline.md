# Outline — Ticket 029 (REDIRECTED)

Implementation structure for the maestro→user **conversational decision
channel**. Ordering matters where noted (a field must exist before its
migration/restore reads it).

## Build order (dependency-correct)

```
0. PREREQ (Ticket 021): `user` is a first-class router sink
   (no-queue, forwards to NotificationDispatcher, returns a failure signal).
   029 builds ON this — do not fork a second delivery path.
        │
        ▼
1. State: persist awaiting_decision
        │
        ▼
2. Emit path: request_decision{to:user} (permission + route + set flag + truncate)
        │
        ▼
3. Clear path: user-sourced reply clears the flag
        │
        ▼
4. Liveness: scheduler skips an awaiting entity + nudge cadence
        │
        ▼
5. Retire gates: deny AskUserQuestion; (optional) dismiss-guard
        │
        ▼
6. Re-mechanize 019 onto this channel
```

## 1 — Persist `awaiting_decision` (order-sensitive)

```
src/hive/models/entity.py        add  awaiting_decision: bool = False  (+ last_nudged_at: float|None)
                                 ↑ add the field FIRST, or _row_to_entity gets an unknown kwarg
src/hive/bus/migrations/029_entity_awaiting_decision.sql   ALTER TABLE entities
                                 ADD COLUMN awaiting_decision BOOLEAN NOT NULL DEFAULT 0
src/hive/bus/entity_store.py     upsert(): write the column; _row_to_entity(): read it
ProcessManager.restore           rehydrate the flag (it already round-trips other fields)
```

## 2 — Emit: `request_decision{to:user}`

```
src/hive/bus/permissions.py      can_request_decision: allow maestro→"user"
src/hive/process/message_dispatcher.py  request_decision branch:
   - if action.to == "user": route via the 021 user-sink (NotificationDispatcher),
     NOT _entities.get(); on dispatch failure return a _reject_action-style signal
   - resolve self.<team>/<maestro>.<team> via the SHARED 031 alias resolver
   - set entity.awaiting_decision = True
   - TRUNCATE the remaining actions in the block (ask-then-end; audit the truncation)
```

## 3 — Clear: only a user-sourced reply

```
src/hive/bus/router.py           (option A) add from_user: bool to Message
                                 (option B, preferred) clear in the USER dispatch path by name,
                                 so the generic router drain never clears it
src/hive/process/<user dispatch> on a user→maestro inbound: entity.awaiting_decision = False
                                 (a peer→maestro message must NOT clear it)
```

## 4 — Liveness: scheduler skip + nudge

```
src/hive/process/scheduler.py    run_once / run_once_for: skip if
                                 is_parked_at_gate(e) OR e.awaiting_decision
                                 (separate check — do NOT overload is_parked_at_gate)
nudge                            reuse the 3600s cadence (gate_coordinator precedent)
                                 with a last_nudged_at guard so the question re-pings,
                                 not goes silent forever
restart-window race             on restore: if the entity has queued user mail,
                                 clear awaiting_decision (let the queued reply re-arm)
```

## 5 — Retire native gates for maestros

```
src/hive/process/tool_policy.py  _MAESTRO_DENY += "AskUserQuestion"
                                 (ExitPlanMode already denied)
CONFIRM-ON-BINARY                verify bare-name denial blocks emission
                                 (ExitPlanMode precedent says yes)
optional dismiss-guard           ONLY if denial leaks: in pty_session, on a detected
                                 stray gate, inject Esc + feed back "use request_decision".
                                 The detector (gates.py) stays; the bridge's
                                 translate/inject/park path is removed.
```

## 6 — Re-mechanize 019

```
019 phase-confirmation = at the phase boundary, emit request_decision{to:user}
("confirm before I advance?") + set awaiting_decision. No native gate.
Update docs/tickets/019-*/ticket.md acceptance accordingly (this PR).
```

## Tests

```
tests/.../test_message_dispatcher.py   maestro request_decision{to:user}:
   permission allowed · routes to NotificationDispatcher · sets awaiting_decision ·
   returns failure on dispatcher error · truncates trailing actions
tests/.../test_entity_store.py         awaiting_decision round-trips upsert→restore
tests/.../test_scheduler.py            an awaiting_decision entity is skipped by the poke
tests/.../test_permissions.py          maestro→user allowed; lead→user still denied
tests/.../test_tool_policy.py          AskUserQuestion in _MAESTRO_DENY
clear-path test                        user reply clears the flag; a peer message does NOT
```

## Risks / watch-items

- **Order trap:** add the `Entity` field before the migration/`_row_to_entity`.
- **One delivery path:** route `to:user` through 021's sink, never a bypass.
- **One alias resolver:** share 031's, don't fix `self` in only one branch.
- **Behaviour, not deletion (S6):** needs a deployed re-smoke
  (maestro→user `request_decision` round-trip), not just green units.
