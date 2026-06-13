# Plan — Ticket 028: pending-gate guard  (issue #125)

**Lane:** direct (one cohesive guard, single PR). The full spec lives in
[#125](https://github.com/yehezkieled/hive/issues/125); see `design.md` /
`outline.md` for the rationale and code sketch.

One-line: refuse to inject a new-turn prompt into the PTY of an entity parked at
an interactive gate, at the shared `send_to_entity` chokepoint, keyed on
`gate_coordinator.pending_request_id`.

## Files this Ticket creates / modifies

| Path | Op | Step |
|------|----|------|
| `src/hive/process/manager.py` | modify | Add `is_parked_at_gate(name) -> bool` helper (None-guarded `pending_request_id`). |
| `src/hive/process/message_dispatcher.py` | modify | Guard at top of `send_to_entity` (after `entity is None`, before `last_activity`/drain): if parked → log + return `/approve` notice, skip `send_turn`. |
| `src/hive/process/scheduler.py` | modify | `run_once`: skip parked maestros before building facts. `run_once_for`: same, return notice for `/eval`. |
| `tests/process/test_message_dispatcher.py` | modify | `StubManager` gets `gate_coordinator`/`is_parked_at_gate`; add parked-skip + not-parked-proceeds tests. |
| `tests/test_scheduler.py` | modify | Add parked-maestro skip test + `/eval`-parked test. |

## Verification

- `ruff check src/ tests/ && ruff format --check src/ tests/`
- `pytest tests/process/test_message_dispatcher.py tests/test_scheduler.py`
- `pytest -m "not integration"` (full unit suite green)
- Behavioural checks proving acceptance:
  - parked entity ⇒ `adapter.send_turn` **not** awaited, gate row **not**
    resolved, return value carries the request id + `/approve` guidance;
  - queued peer message **survives** a refused send (inbox not drained);
  - not-parked entity ⇒ turn runs normally (regression).

## Out of scope

- Bridging the gate to the user — Ticket 029.
- `waitingFor` session-state fallback — deferred; `is_parked_at_gate` is the seam.
- No-progress timeout — Ticket 027 (absorbed into 017).
- Maestro "prefer async questions over blocking menus" — input for 029/021.

## Cross-cutting impact

None. No reference-doc (`README` / `DEPLOYMENT` / `ARCHITECTURE`) edits, no new
CONTEXT.md term, no new ADR — this extends ADR 0004's gate bridge with a safety
invariant, recorded in `design.md`.

## Build

Direct lane: one branch off `main`, implement the table above, open a PR that
**closes #125**, squash-merge on green CI. Then deploy per CLAUDE.md
(`git push` from the main repo · `systemctl --user restart hive.service` ·
verify on the Tailscale IP).
