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

## Decision 2 — uvicorn server task → tracked + gracefully cancelled

The fix has two halves: make the task reachable, and stop it on shutdown.

The blocker is scope: `background_tasks` is declared at `:385`, *after* the
`if WEB_PORT > 0:` block that creates the server at `:370-371`, and `server` is
local to that block. So:

1. **Hoist the holders above the web block** (to ~`:319`, before
   `# Start web dashboard if configured`):
   ```python
   server: uvicorn.Server | None = None
   background_tasks: list[asyncio.Task] = []  # type: ignore[type-arg]
   ```
   (Move the existing `background_tasks` declaration up; the auto-management
   appends at `:387-406` still work — it's just declared earlier.)
2. **Inside the web block**, assign the outer `server` and append its task:
   ```python
   server = uvicorn.Server(config)
   background_tasks.append(asyncio.create_task(server.serve()))
   ```
3. **In the cleanup** (`:411`, before the cancel loop), signal graceful exit:
   ```python
   if server is not None:
       server.should_exit = True
   for task in background_tasks:
       task.cancel()
   ```

`should_exit = True` lets uvicorn unbind the socket cleanly; the existing
`task.cancel()` loop then covers the server task too as a hard backstop. This
is the "`should_exit` / `task.cancel()`" the ticket calls for.

**Alternatives rejected:**
- *`task.cancel()` only (no `should_exit`)* — works (uvicorn closes sockets on
  `CancelledError`) but skips the documented graceful path; one extra line buys
  the clean unbind the WHY cares about.
- *Dedicated `server_task` handle + `await` with timeout* — more precise
  shutdown ordering, but more code and a new await in the hot shutdown path.
  Not worth it: joining the existing `background_tasks` list reuses the cancel
  machinery already there.

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
