# 025 — Worktree crash-recovery: entities re-adopt their worktrees

## What

Make worktree ownership survive a Hive crash. At service startup,
reconcile `git worktree list` against the restored entity set:

1. **Re-adopt** — a restored Lead/Worker whose worktree already exists
   on disk picks it up again (same path, same branch, work preserved).
   The lazy idempotent `create()` path is believed to cover the
   entity-was-persisted case (023 research Q3) — verify it covers
   *every* crash point, including dies-mid-spawn (worktree created,
   entity not yet persisted) and dies-mid-kill (entity gone, worktree
   remains).
2. **Orphan policy** — a worktree with no owning entity is surfaced
   (audit + log), then removed or adopted per a deliberate policy, not
   left to accumulate silently. Today nothing sweeps; orphans are
   invisible unless someone runs `git worktree list` by hand.

## Why

From the 023 design grill (2026-06-11, fork 5b). 023 activates the
worktree floor; this ticket makes it crash-safe. A lead that dies
mid-task should resume *its own* worktree — not strand half-finished
work in a directory nothing references.

## Acceptance

- Startup reconciliation runs once, after entity restore.
- A restored Lead's adapter cwd is its pre-crash worktree
  (`/proc/<pid>/cwd` matches; branch intact; uncommitted edits
  preserved).
- A true orphan (no owning entity) is audited and handled per the
  chosen policy; none accumulate silently.
- Hermetic tests cover the crash-point matrix: mid-spawn, mid-kill,
  normal restart.
- `ruff` + `pytest -m "not integration"` green.

## Non-goals

- The floor wiring + session pinning themselves (023).
- The commit→PR→merge→remove *discipline* for changed leaf-agent
  worktrees — 016 ships it as lead-JD policy (016 design D5). What 025
  **does** inherit as candidate scope: CC-created leaf worktrees sit
  outside `WorktreeManager`'s bookkeeping, so the orphan policy above
  should also sweep siblings a forgetful lead stranded.
- Backup/restore of worktree contents beyond what git already keeps.

## Notes

Opened from the 023 grill (fork 5b). S6 candidate. Depends on 023
(floor must be live for worktrees to exist in production at all).
