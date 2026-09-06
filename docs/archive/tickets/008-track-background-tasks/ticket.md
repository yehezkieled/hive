# 008 — Track untracked fire-and-forget tasks

## What

Two background `asyncio` tasks are created with a bare
`asyncio.create_task(...)` and never tracked or cancelled:

- the gate-waiting notification in `process/approval_handler.py:457`
  (`_on_gate_state`), and
- the uvicorn web-server task in `__main__.py:371`.

Track both in a set (with an `add_done_callback(...discard)`) the way
`_wake_tasks` / `_kickoff_tasks` already are, and cancel the server
task on shutdown.

## Why

The event loop holds only a *weak* reference to a bare task, so an
untracked one can be garbage-collected mid-flight — the gate
notification can silently never fire, so the user is never told an
Entity is parked at a gate. The uvicorn task is additionally never
cancelled on shutdown, so it can keep the port bound on SIGTERM and
slow the systemd restart the deploy runbook depends on. Both are the
same bug class; every other background task in the codebase is already
tracked.

## Acceptance

- The gate-notification task is held in a tracking set + discarded on
  completion; it cannot be GC'd mid-flight.
- The uvicorn server task is tracked and cancelled (`should_exit` /
  `task.cancel()`) in the shutdown path.
- A test covers the gate-notification task lifetime.
- `ruff check` + `ruff format --check` + full `pytest -m "not integration"`
  green.
