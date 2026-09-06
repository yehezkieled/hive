# Design — Ticket 008: Track untracked fire-and-forget tasks

Two independent fixes, same bug class. Both follow the codebase's existing
task-tracking convention (see [`research.md`](research.md)). Zero behaviour
change on the happy path — the tasks already run; this only stops them being
lost.

## Decision 1 — gate-notification task → manager-owned set

Add a third tracking set to `ProcessManager`, beside the two that exist:

```python
# process/manager.py __init__, next to _wake_tasks (:143)
self._gate_tasks: set[asyncio.Task] = set()
```

Replace the bare create at `approval_handler.py:457` with the standard idiom:

```python
if state == "gated":
    task = asyncio.create_task(self._notify_gate_waiting(entity_name))
    self._mgr._gate_tasks.add(task)
    task.add_done_callback(self._mgr._gate_tasks.discard)
```

**Alternatives rejected:**
- *Handler-local set on `ApprovalHandler`* — breaks the settled "shared task
  state lives on the manager, collaborators reach it via `self._mgr`"
  convention (`_wake_tasks`, `_kickoff_tasks`). No upside.
- *`asyncio.TaskGroup`* — a structured-concurrency scope that would have to
  wrap the handler's lifetime. Overkill for one detached notify; the set +
  `discard` idiom is what the rest of the code uses.

## Decision 2 — uvicorn server task → tracked + cancelled

Track the server task in the existing `background_tasks` list so the existing
shutdown loop cancels it. The blocker is scope: `background_tasks` is declared
at `:385`, *after* the `if WEB_PORT > 0:` block that creates the server at
`:370-371`. So:

1. **Hoist the `background_tasks` declaration above the web block** (to ~`:319`,
   before `# Start web dashboard if configured`). The auto-management appends
   at `:387-406` still work — the list is just declared earlier.
   ```python
   background_tasks: list[asyncio.Task] = []  # type: ignore[type-arg]
   ```
2. **Inside the web block**, append the server task instead of dropping it:
   ```python
   server = uvicorn.Server(config)
   background_tasks.append(asyncio.create_task(server.serve()))
   ```
3. **Cleanup is unchanged** — the existing `for task in background_tasks:
   task.cancel()` loop (`:411`) now cancels the server task too.

`server` stays local to the web block; nothing outside it needs the reference.

**Why no `server.should_exit = True` on shutdown** (this was tried and dropped —
the adversarial review caught it as dead code, verified empirically against
uvicorn 0.47.0): setting `should_exit` and then *immediately* calling
`task.cancel()` with no `await` between them is a **no-op**. The serve task is
parked in uvicorn's `main_loop` `await asyncio.sleep(0.1)`; the `CancelledError`
fires there before `on_tick` re-reads `should_exit`, so `await self.shutdown()`
(the line that closes the listening socket) never runs. Behaviour is identical
to cancel-only. The graceful close would only help if we *awaited* the server
task before cancelling — and that buys little here: on SIGTERM `main()` returns,
`asyncio.run()` tears the loop down, and the OS frees the port on process exit
regardless (uvicorn also sets `SO_REUSEADDR`, so rebind isn't blocked). The real
win is simply that the task is no longer untracked: no GC risk, no "Task was
destroyed but it is pending" warning, a clean cancel. That satisfies the
acceptance ("tracked **and cancelled**").

**Alternatives rejected:**
- *`should_exit = True` before the cancel loop (no await)* — dead code, as
  above. Misleading; dropped.
- *Dedicated `server_task` handle + `await asyncio.wait_for(..., timeout=5)`
  before cancel* — this is the *only* way to actually run uvicorn's graceful
  socket close, and it works. But it adds shutdown latency for a benefit the
  deploy doesn't need (port frees on process exit anyway; `SO_REUSEADDR`).
  Out of scope; revisit only if in-flight HTTP request draining on restart
  becomes a real requirement.

## Follow-up flagged (out of scope for 008)

The adversarial review surfaced a **pre-existing** latent risk in the same
shutdown path: uvicorn's `serve()` runs inside `capture_signals()`, which can
override the app's own `SIGINT`/`SIGTERM` handler (`loop.add_signal_handler`
sets `stop_event`). If uvicorn's handler wins, `stop_event` may never be set on
`SIGTERM`, so `await stop_event.wait()` never returns and the cleanup block
never runs at all. This predates 008 and is orthogonal to it — not fixed here,
but worth its own ticket since it undercuts the deploy runbook's restart
assumption. Verify any shutdown claim via a real `SIGTERM` on the deployed
service (confirm the "Shutting down…" log fires), not a loopback/unit check.

## Side effects

- **`CONTEXT.md`** — none. No new term.
- **ADR** — none. This applies an existing decision, it doesn't make a new one.
- **Reference docs** — none. Not cross-cutting.

## Test

Extend `tests/process/test_approval_handler.py`:
- Add `self._gate_tasks: set[asyncio.Task] = set()` to `StubManager.__init__`
  (mirrors the real manager surface the handler now touches).
- New `@pytest.mark.asyncio` test: call `_on_gate_state(name, "gated")`, assert
  the task is in `_gate_tasks` while in-flight, `await` it, then assert the
  done-callback discarded it (set is empty). This proves the GC-safety
  property, not just that the notification fires (the existing `:415` test
  already covers the dispatch).
