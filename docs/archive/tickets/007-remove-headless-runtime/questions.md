# 007 — Questions (what to find out)

The unknowns going into the ticket. Each is answered in
[`research.md`](research.md); kept here as the record of what was open.

## Scope

1. **What actually *is* the "headless path"?** Just the adapter's
   `use_pty=False` branch + `ClaudeSession` + the flag — or does it reach
   further into the entity lifecycle?
2. **Is `ClaudeSession` referenced anywhere beyond the adapter?** The
   acceptance criterion is `grep ClaudeSession = 0` in `src/` + `tests/`.
3. **What does 004 (manager breakup, landed first) leave for 007 to edit?**
   Which module now owns the spawn/session/capacity machinery, and did 004
   add anything (shims, smoke tests) that 007 must unwind?

## Behaviour / safety

4. **Is the legacy `spawn_entity` / `_sessions` / `active_count` /
   preemption machinery live in PTY production, or dead?** If dead, removing
   it is a non-event; if live, removal changes behaviour.
5. **Can the headless path be removed with zero behaviour change** (the
   sprint's stated constraint), or does deleting `ClaudeSession` force a
   behaviour delta somewhere?
6. **Does PTY conversation continuity depend on the headless `--resume` /
   `initial_session_id` logic?** If so, deleting it breaks resume.

## Test harness (the crux, per `ticket.md`)

7. **Why does the unit suite pin `HIVE_USE_PTY=false`?** What breaks if the
   pin is removed before the suite is rebased?
8. **At which seam should the suite mock the harness** once the subprocess
   path is gone — `ClaudeSession`, `PtySession`, or the `ClaudeAdapter`
   boundary?
9. **What is the fate of the preemption / max-sessions tests** that exercise
   `spawn_entity`?

## Cross-cutting

10. **Which reference docs mention the headless path** and must be updated
    in this (cross-cutting ✱) ticket — and which `claude -p` mentions refer
    to the *advisor's* one-shot call, which stays?
11. **Does this decision warrant an ADR**, and what number?
