# Outline — Ticket 028

Implementation structure. One vertical change (a pending-gate guard) across
three production seams + a test fixture + tests. Direct lane, single PR.

## Production

### 1. `ProcessManager.is_parked_at_gate` — new helper (`process/manager.py`)

```python
def is_parked_at_gate(self, entity_name: str) -> bool:
    """True while a Turn is parked on an interactive gate for this entity.

    The coordinator-owned source of truth: pending_request_id is non-None
    exactly between gate-park and gate-resume, for every gate kind.
    """
    gc = self.gate_coordinator
    return gc is not None and gc.pending_request_id(entity_name) is not None
```

Single home for the None-guard and the future `waitingFor` fallback.

### 2. `MessageDispatcher.send_to_entity` — the guard (`process/message_dispatcher.py`)

Insert after the `entity is None` KeyError check (`~:101-103`), before
`last_activity` update and the inbox drain:

```python
if self._mgr.is_parked_at_gate(entity_name):
    request_id = self._mgr.gate_coordinator.pending_request_id(entity_name)
    logger.info("send_to_entity: %s parked at gate %s — not injecting",
                entity_name, request_id)
    return (f"<{entity_name} is parked at gate {request_id}; answer it with "
            f"/approve gate {request_id} or /deny gate {request_id}>")
```

### 3. `PriorityScheduler` — defensive skip (`process/scheduler.py`)

`run_once` loop (`~:219-224`): skip before building the facts prompt.

```python
for m in maestros:
    if pm.is_parked_at_gate(m.name):
        logger.info("scheduler: skipping %s — parked at a gate", m.name)
        continue
    facts = await self.build_facts_prompt(m.name)
    await pm.send_to_entity(m.name, facts)
    poked.append(m.name)
```

`run_once_for` (`~:236-237`): same guard; return an early notice string so
`/eval` on a parked maestro reports the gate instead of poking it.

## Test fixture

### 4. `StubManager` (`tests/process/test_message_dispatcher.py`)

Add `self.gate_coordinator = None` in `__init__` (and, since the guard calls the
facade helper, a `def is_parked_at_gate(self, name): return False` — or set a
drivable fake). Without it, the new attribute access breaks existing full-turn
tests (research §11).

## Tests

### 5. `tests/process/test_message_dispatcher.py`

- `test_send_to_entity_skips_parked_gate`: `is_parked_at_gate` → True ⇒
  `adapter.send_turn` **not** awaited; return value contains the request id +
  `/approve`; inbox **not** drained (queued peer message survives).
- `test_send_to_entity_proceeds_when_not_parked`: → False ⇒ normal turn runs
  (`send_turn` awaited). Regression guard.

### 6. `tests/test_scheduler.py`

- `test_run_once_skips_maestro_parked_at_gate`: one parked + one free maestro ⇒
  only the free one in `poked`; `send_to_entity` never called for the parked one.
- `test_run_once_for_parked_returns_notice`: `/eval` on a parked maestro does
  not call `send_to_entity`.

## Verification

```
ruff check src/ tests/ && ruff format --check src/ tests/
pytest tests/process/test_message_dispatcher.py tests/test_scheduler.py
pytest -m "not integration"
```

## Out of scope

- Bridging the gate to the user (Ticket 029).
- The `waitingFor` session-state fallback (deferred; helper is the seam).
- The no-progress timeout (Ticket 027 / absorbed into 017).
