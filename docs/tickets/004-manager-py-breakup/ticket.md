# 004 — Break up `process/manager.py`

## What

Split `src/hive/process/manager.py` (2,308 LOC, one `ProcessManager`
class with 47 methods) into a thin core plus four focused modules.
Zero behaviour change. All existing tests pass unmodified.

## Why

`manager.py` is the largest single navigability and test-isolation
blocker in the codebase. Nine distinct responsibilities live inside
one class — entity lifecycle, message dispatch, action parsing,
approval workflows, wake-on-inbound scheduling, adapter pooling,
status/health, persistence, and hierarchy traversal. Every change
costs disproportionate context. Phase 2's "Restructure" theme exists
to fix exactly this.

The QuotaMonitor split (commit `30fa909`) proved the pattern works
inside Hive: extract pure-logic state machines, extract
text/formatting, keep the orchestrator thin. Apply the same approach
here at larger scale.

## Acceptance

- `process/manager.py` shrinks to ≈400 LOC of orchestration only
  (init, persist, audit, dict/lock management, status/health).
- Four new sibling modules under `src/hive/process/`:
  `approval_handler.py`, `wake_scheduler.py`, `message_dispatcher.py`,
  `lifecycle_manager.py`.
- Each new module has its own test file with isolated unit tests.
- No public-API breakage — every existing import path that points at
  `manager.py` still works.
- `ruff check`, `ruff format --check`, full `pytest` all green.
- A maestro turn (Telegram + web) completes end-to-end on the
  refactored code — smoke test, not test-suite-only.

## Sprint

Committed to Sprint **2026-Q2-S3** (2026-06-01 → 2026-06-15) as the
centrepiece. Research is captured (see `research.md`); `design.md`,
`outline.md`, `plan.md` get authored when the ticket is grabbed. Note:
manager.py is now 2,469 LOC (grew from the 2,308 measured here after
the Ticket 003 gate-wiring landed) — re-measure at design stage.
