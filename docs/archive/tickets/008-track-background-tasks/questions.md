# Questions — Ticket 008: Track untracked fire-and-forget tasks

The unknowns going in. Each is answered in [`research.md`](research.md) with
code evidence, not guesses.

## Tracking pattern

1. Where should the gate-notification task be tracked — a set on
   `ProcessManager` (the way `_wake_tasks` / `_kickoff_tasks` already are), or
   a set local to `ApprovalHandler`?
2. What exactly is the existing idiom — `set.add(task)` +
   `task.add_done_callback(set.discard)` — and do we copy it verbatim?

## uvicorn server task

3. Is the uvicorn server task cancelled anywhere on shutdown today?
4. Where is the server task created, and what is its variable scope — can the
   shutdown path even reach it?
5. What is the correct way to stop a `uvicorn.Server` from *outside* its own
   signal handler — `should_exit`, `Task.cancel()`, or both?
6. Is there already a `background_tasks` cleanup loop the server task can join,
   or does it need its own handle?

## Test seam

7. Can the gate-notification task's lifetime (tracked while in-flight,
   discarded on completion) be asserted hermetically — no real
   `ProcessManager`, no Postgres?

## Scope / impact

8. Is this cross-cutting — does it touch a reference doc (`README`,
   `DEPLOYMENT.md`, `ARCHITECTURE`), introduce a `CONTEXT.md` term, or warrant
   an ADR?
