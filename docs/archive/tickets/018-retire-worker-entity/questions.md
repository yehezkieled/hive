# Questions — Ticket 018: Retire the persistent Worker entity

The unknowns going in. `research.md` answers them with file refs; `design.md`
makes the calls. Grouped by the decision they unblock.

## Surface & ordering (drives the lane + blocker graph)

1. **What is the full Worker footprint?** Every site touching the `Worker`
   type, the `spawn_worker` action, the `/worker` command, persistence,
   dashboard, and tests — with file:line. (Sweep output → `research.md`.)
2. **Safe deletion order.** A deletion has import-order constraints: a usage
   that imports `Worker` must go before the class itself, or the tree won't
   import. What is the leaf→root order, and does it cut into clean vertical
   slices (fan-out) or one coupled sweep (direct)?
3. **Is `Worker` sharing any base/abstraction with `Maestro`/`Lead`?** If the
   three share an `Entity` base or a role enum, deletion is surgical, not a
   file `rm`. What survives?

## The 016 denial stubs — delete or keep a graceful reject?

4. 016 left `can_spawn_worker` **denying unconditionally** and `actions.py`
   **parsing `spawn_worker` only to reject it**. Does 018 delete those
   entirely, or keep a tolerant "unknown/retired action → reject with
   feedback" path? **Constraint:** if a stale maestro/lead prompt still emits
   `spawn_worker` post-deletion, the turn must *reject gracefully*, not crash
   the action parser. Which is it?

## Persistence — the orphaned-state risk

5. How is a Worker's state persisted today (vault / JSON state file / org
   tree)? After the type is deleted, does startup **crash** trying to
   deserialize a pre-existing on-disk Worker record? Need a tolerant loader
   or a one-time cleanup/migration at deploy?
6. Are there live Worker stragglers on the deployed host to kill at cutover
   (`/worker kill`) before the command itself is removed?

## The `/worker` command

7. 016 kept `/worker kill` to reap stragglers. Once Workers can't exist, does
   the **whole `/worker` command** go, or does `kill` survive to target other
   entities? Confirm `kill`/`list` don't operate on Leads/Maestros in a way
   something still needs.

## Dashboard

8. Does removing Worker rendering leave the org tree coherent with **017's
   Workflow-run progress cards**? Any rendering shared with Lead nodes that
   must not break?

## Tests — delete vs repoint

9. Which Worker tests are **dead** (test spawn/lifecycle that no longer
   exists → delete) vs **leaf-dispatch integration tests** that must be
   **repointed** to the Workflow engine and keep passing? The repointed set
   is what proves the DoD.

## Glossary

10. **`CONTEXT.md` "Worker" term: delete or redefine?** A "Leaf agent" term
    already exists. Options: (a) delete "Worker" outright, (b) keep it as a
    tombstone pointing at "Leaf agent", (c) redefine "Worker" → the ephemeral
    Leaf agent. Decide in `design.md`. ADR 0013 already records the *decision*
    to retire; this is glossary hygiene only.

## Sequencing vs 016

11. 018's acceptance repeats 016's live DoD ("a maestro turn completes
    end-to-end with leaf work on Workflow"). Can 018 ship on the **same live
    smoke** as 016's close-out, or does it need its own? (They likely share
    one run: delete → deploy → one maestro→lead→leaf smoke proves both.)
