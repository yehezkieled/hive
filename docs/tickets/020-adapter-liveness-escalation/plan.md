# Plan — Ticket 020: auto-bounce jammed PTY sessions  (issue #147)

Direct lane — one branch, one PR that closes #147. Recover a jammed PTY
Entity automatically (kill → respawn with conversation preserved →
notify with a reason), guarded by two liveness safety checks, with a
time-windowed flap-guard. Approach in `design.md`; build steps in
`outline.md`.

## Files this Ticket creates / modifies

| Path | Op | Step |
|------|----|------|
| `src/hive/process/manager.py` | modify | `_liveness` state dict; `_bounce_adapter(entity, reason)` helper; `BOUNCE_STALL_THRESHOLD`/`BOUNCE_FLAP_MAX`/`BOUNCE_FLAP_WINDOW_S` constants |
| `src/hive/process/message_dispatcher.py` | modify | wrap `adapter.send_turn` (`:218`) in `try/except TimeoutError`; safety checks (`is_parked_at_gate`, `workflow_active`); stall count; bounce-and-retry-once; reset on success — mirror the auto-compact block |
| `src/hive/runtime/pty_session.py` | modify | surface `status`/`waitingFor` from the session-state file (reason source only; not the decision) |
| `src/hive/process/manager.py` (or a `runtime/` util) | modify | `_bounce_reason(entity, adapter)` best-effort assembler |
| `tests/fakes.py` | modify | `FakeAdapter.send_turn` variant that scripts a `TimeoutError` sequence |
| `tests/process/test_message_dispatcher_bounce.py` (or extend existing) | create | tests 1–7 from `outline.md` (one bounce, success-reset, gate check, workflow check, flap give-up, window reset, reason-in-message) |
| `models/entity.py` | verify | `RUNNING/GATED → ERROR` give-up transition is legal (no change expected) |

## Verification
- `ruff check src/ tests/ && ruff format --check src/ tests/`
- `pytest -m "not integration"` (hermetic — `FakeAdapter`, no real PTY)
- Deployed re-smoke (S6 DoD): force a jam on a test entity, confirm an
  `entity.bounce` audit row, a Telegram notification carrying a reason, and
  a recovered next turn — checked from the Tailscale IP, not loopback.

## Out of scope
- Changing the reader's 180s no-progress timeout (it stays as-is).
- Detecting/bridging the permission prompt (ADR 0005 — no signature).
- Preventing the trigger (Ticket 022) and per-Entity credential isolation
  (Phase 5).
- Steering a running Workflow (S7).

## Cross-cutting impact (declared up front)
- **CONTEXT.md** — new glossary term **Auto-bounce** (added in this Ticket).
- **docs/adr/0015-auto-bounce-jammed-sessions.md** — new ADR (added in this
  Ticket).

## Dependencies / sequencing
- Uses only the **public** `is_parked_at_gate` contract (Ticket 028);
  assumes post-029 gate semantics. 029 is in flight in a separate worktree
  — different files; only the notification path is shared and 020 just
  *calls* `_notify`/`_audit`.
- Soft-pairs with 030: the `workflow_active` safety check covers 030's
  false-timeout class, so 020 does not block on 030. Clean order is
  029 → 030 → 020, but 020 can build in parallel and rebase.
