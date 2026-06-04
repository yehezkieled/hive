# Plan — Ticket 008: Track untracked fire-and-forget tasks

Direct lane — one branch, one PR. Bring two bare `asyncio.create_task(...)`
calls under the codebase's existing GC-tracking convention, and cancel the
uvicorn server on shutdown. No behaviour change on the happy path.

## Files this Ticket creates / modifies

| Path | Op | Step |
|------|----|------|
| `src/hive/process/manager.py` | modify | Add `self._gate_tasks: set[asyncio.Task] = set()` in `__init__`, beside `_wake_tasks` (~:143). |
| `src/hive/process/approval_handler.py` | modify | In `_on_gate_state` (:457), capture the notify task, `self._mgr._gate_tasks.add(task)`, `task.add_done_callback(self._mgr._gate_tasks.discard)`. |
| `src/hive/__main__.py` | modify | Hoist the `background_tasks` list above the `if WEB_PORT > 0:` block (:385 → ~:319); inside the block, `background_tasks.append(asyncio.create_task(server.serve()))` instead of dropping the task (`server` stays local). The existing cleanup cancel loop (:411) now cancels it too. No `should_exit` — see `design.md` (it's a no-op without an await, and the port frees on process exit anyway). |
| `tests/process/test_approval_handler.py` | modify | Add `self._gate_tasks: set[asyncio.Task] = set()` to `StubManager`; add an async test asserting the gate task is tracked while in-flight and discarded after it completes. |

No change to the `_notify_gate_waiting` body, the uvicorn `Config`, or any
other collaborator.

## Verification

Run from the worktree root. `python` is not on PATH here (Ticket 009); use the
repo interpreter and put the worktree's `src` first so you test *this* code:

- `PYTHONPATH=src /home/hezki/projects/hive/.venv/bin/python -m pytest tests/process/test_approval_handler.py -q`
  → the new gate-task lifetime test passes; the existing gate tests stay green.
- `/home/hezki/projects/hive/.venv/bin/ruff check src/ tests/ && /home/hezki/projects/hive/.venv/bin/ruff format --check src/ tests/`
  → clean (CI runs these as separate gates).
- `PYTHONPATH=src /home/hezki/projects/hive/.venv/bin/python -m pytest -m "not integration" -q`
  → full suite green.
- Behavioural checks (assert, don't assume):
  - **Gate task:** after `_on_gate_state(name, "gated")` the new task is in
    `_gate_tasks`; after it completes the `add_done_callback` discards it —
    proves it can't be GC'd mid-flight.
  - **Server shutdown:** on SIGTERM the uvicorn task is in `background_tasks`
    and gets `task.cancel()`d in the cleanup loop — no untracked task left
    pending on shutdown. (The port itself frees on process exit regardless;
    this change does not add a graceful socket close — see `design.md`.) Smoke
    from the Tailscale IP, not just loopback.

## Out of scope

- The other long-lived `background_tasks` (idle_checker, daily_summary,
  heartbeat, scheduler, health_monitor) — already tracked and cancelled.
- Refactoring `_on_gate_state` or `_notify_gate_waiting` logic.
- An `asyncio.TaskGroup` migration for any task set (rejected in `design.md`).
- A graceful uvicorn socket close (`should_exit` + `await` the server task
  before cancel) — rejected in `design.md`; the port frees on process exit.
- The pre-existing `capture_signals()` vs app `SIGTERM`-handler risk — flagged
  in `design.md` as a separate follow-up, not fixed here.

## Cross-cutting impact

None. No reference-doc edit (`README`, `DEPLOYMENT.md`, `ARCHITECTURE`), no
`CONTEXT.md` term, no ADR. The fix follows the existing `_wake_tasks`
convention; the deploy procedure is unchanged.

## Build

Direct lane — implement the four file ops on this branch, run the verification
gate, open one PR. No fleet Workflow needed.
