# 020 — Outline

Implementation structure for the direct-lane build. One PR. Order is
test-first within each unit.

## Module changes

### 1. `process/manager.py` — state + a bounce helper
- Add `self._liveness: dict[str, dict] = {}` beside `_compacting`
  (entry: `{"stalls": int, "bounces": collections.deque}`).
- Add `async def _bounce_adapter(entity, reason) -> bool`: `adapter.stop()`
  → `_adapters.pop(name)` → `_get_or_create_adapter(entity)` →
  `_notify` + `_audit("entity.bounce")`. Returns False (and does the
  give-up path) when the flap-guard trips.
- Module constants: `BOUNCE_STALL_THRESHOLD=2`, `BOUNCE_FLAP_MAX=3`,
  `BOUNCE_FLAP_WINDOW_S=1800` (mirror the `AUTO_COMPACT_*` style).

### 2. `process/message_dispatcher.py` — the catch at the chokepoint
- Wrap `adapter.send_turn(prompt)` (`:218`) in `try/except TimeoutError`.
- On `TimeoutError`: run the two safety checks (`is_parked_at_gate`,
  adapter `workflow_active`). If either holds off → re-raise / return the
  existing timeout path unchanged (no stall counted). If both clear →
  `stalls += 1`; at threshold call `_bounce_adapter` and **retry the send
  once** on the fresh adapter.
- On success → reset `_liveness[name]["stalls"] = 0`.
- Mirror the auto-compact block's structure (`:221-248`).

### 3. `runtime/pty_session.py` — surface `status`/`waitingFor`
- Extend `_parse_session_id` (or add `_parse_session_state`) to also read
  `status` / `waitingFor`. Expose a small read used only by the reason
  assembler — **not** by the bounce decision.

### 4. Reason assembler (small helper, manager or a `runtime/` util)
- `_bounce_reason(entity, adapter) -> str`: session-state `waitingFor` →
  `is_alive` → last transcript entry → "no output for N min — cause
  unknown". Best-effort; never raises.

### 5. `models/entity.py` — give-up transition
- Confirm `RUNNING/GATED → ERROR` is a legal transition for the give-up
  path (it is, per the state machine); no new state needed.

## Test plan (`tests/`, hermetic — no real PTY)

Using `FakeAdapter` + `using_adapter()` (extend `FakeAdapter.send_turn` to
script a `TimeoutError` sequence):

1. **One bounce on threshold** — 2 stalls (both checks clear) → exactly one
   `_bounce_adapter`, one `entity.bounce` audit, one notification; the
   retry succeeds.
2. **Success resets** — stall, success, stall → no bounce (counter reset).
3. **Gate safety check** — `is_parked_at_gate` True at timeout → no stall,
   no bounce (the maestro-waiting-7-min case; also the 029 regression
   guard).
4. **Workflow safety check** — `workflow_active` True at timeout → no
   stall, no bounce (the 030 false-timeout case).
5. **Flap-guard** — M bounces within W → give-up: entity → `ERROR`,
   `entity.bounce_failed` audit, error-kind notification, **no** further
   respawn.
6. **Flap window resets** — bounces spread beyond W do not trip give-up.
7. **Reason in the message** — session-state `waitingFor="permission
   prompt"` → notification/audit carry that reason; absent field →
   "cause unknown" and the bounce still fires.

## Validation gate
`ruff check src/ tests/ && ruff format --check src/ tests/ && pytest -m "not integration"`.

## Build order
state + constants → `_bounce_adapter` (+ tests 1,2) → safety-check wiring
in dispatcher (+ tests 3,4) → flap-guard (+ tests 5,6) → reason assembler
(+ test 7) → CONTEXT.md + ADR 0015.

## Post-merge (deployed re-smoke, per S6 DoD)
Force a jam on a test entity (un-bridged prompt / kill its progress), watch
for: `entity.bounce` audit, a Telegram notification with a reason, and a
recovered next turn — verified from the Tailscale IP, not loopback.
