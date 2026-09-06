# Research — Ticket 008: Track untracked fire-and-forget tasks

Answers to [`questions.md`](questions.md), grounded in the code as of
`09ee173`. Line numbers are anchors, not contracts.

## The established pattern (the template to copy) — Q1, Q2

`ProcessManager` already owns two GC-tracking task sets, declared in
`__init__`:

- `process/manager.py:137` — `self._kickoff_tasks: set[asyncio.Task] = set()`
- `process/manager.py:143` — `self._wake_tasks: set[asyncio.Task] = set()`

Collaborators reach them through `self._mgr` and use one idiom
(`process/wake_scheduler.py:140-141`):

```python
self._mgr._wake_tasks.add(task)
task.add_done_callback(self._mgr._wake_tasks.discard)
```

`process/message_dispatcher.py:532-533` does the same for `_kickoff_tasks`. So
the convention is settled: **the set lives on the manager; the collaborator
adds + self-discards via `self._mgr`.** A handler-local set would break that
convention for no benefit — `ApprovalHandler` already holds `self._mgr`.

## Site 1 — gate-notification task (untracked) — Q3 area

`process/approval_handler.py:457`, inside the sync hook `_on_gate_state`:

```python
if state == "gated":
    asyncio.create_task(self._notify_gate_waiting(entity_name))   # ref dropped
```

`_on_gate_state` is called from `PtySession`'s sync `on_gate_state` hook inside
the event loop, so the notification is deliberately detached. But the return
value is dropped — the loop holds only a **weak** reference, so the task can be
garbage-collected before `_notify_gate_waiting` runs. Failure mode: the user is
never told an Entity is parked at a gate. Same bug class as the tracked tasks
above; this one just never got the treatment.

## Site 2 — uvicorn server task (untracked AND never cancelled) — Q3–Q6

`__main__.py`, inside the `if WEB_PORT > 0:` web-dashboard block (opens at
`:321`):

```python
server = uvicorn.Server(config)        # :370  — local to the if-block
asyncio.create_task(server.serve())    # :371  — ref dropped
```

Then the long-lived task list and its shutdown, lower in the same `if token:`
branch:

- `:385` — `background_tasks: list[asyncio.Task] = []`
- `:387-406` — idle_checker / daily_summary / heartbeat / scheduler /
  health_monitor are appended to it
- `:409` — `await stop_event.wait()`
- `:413` — `for task in background_tasks: task.cancel()`

Two problems, both confirmed:

1. The server task is **not** in `background_tasks`, so the `:413` loop never
   cancels it. On SIGTERM the server keeps the port bound, slowing the
   `systemctl --user restart hive.service` the deploy runbook relies on.
2. `server` is scoped to the `if WEB_PORT > 0:` block, so the `:413` cleanup
   can't even reach it to signal a graceful exit.

**Q5 — how to stop it.** Verified against the installed uvicorn:

```
uvicorn 0.47.0 | should_exit = False | handle_exit = True
```

`uvicorn.Server.should_exit` is a public bool (default `False`) that the
server's own signal handlers flip via `handle_exit`; setting it from outside
makes `serve()` return gracefully and unbind the socket. `Task.cancel()` is the
hard fallback — uvicorn's `serve()` still runs its socket-closing shutdown on
`CancelledError`. The ticket sanctions "`should_exit` / `task.cancel()`"; both
together is belt-and-suspenders (graceful first, forced cancel as backstop).

## Test seam — Q7

`tests/process/test_approval_handler.py` drives `ApprovalHandler` against a
`StubManager` — no Postgres, no real manager (the file's docstring spells out
the seam). The existing `test_on_gate_state_gated_transitions_and_notifies`
(`:415`) already calls `_on_gate_state(..., "gated")` inside a live event loop
(`@pytest.mark.asyncio`) and asserts `notification_dispatcher.dispatch` was
awaited after one `await asyncio.sleep(0)`.

So the lifetime test is a small extension: give `StubManager` a
`_gate_tasks: set` attribute (matching the real manager), assert the task is in
the set while in-flight, await it, then assert the done-callback discarded it.
Fully hermetic.

## Scope / impact — Q8

**Not cross-cutting.** No reference-doc edit (`README`, `DEPLOYMENT.md`,
`ARCHITECTURE`), no new `CONTEXT.md` term, no ADR — the change *follows* the
existing `_wake_tasks` convention rather than introducing a new decision. The
WHY mentions the systemd restart, but that is motivation, not a runbook edit:
the fix makes the deploy faster without changing how deploy is performed.
