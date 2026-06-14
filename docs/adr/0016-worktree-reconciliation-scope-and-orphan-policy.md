# ADR 0016 — Worktree reconciliation: WORKTREES_DIR-only scope, never delete dirty

- **Status:** Accepted
- **Date:** 2026-06-14
- **Ticket:** [025](../tickets/025-worktree-crash-recovery/)

## Context

The worktree floor is live (Ticket 023, ADR 0010): every Team Lead runs in
its own git worktree under `WORKTREES_DIR` (`<repo>/worktrees/<name>`,
branch `hive/<name>`). A Hive crash can leave that geometry inconsistent:

- A restored lead comes back **path-less** — `worktree_path` is never
  round-tripped (`entity_store.py:74` hardcodes `None`, an 018 leftover).
- A crash mid-`create_team` (worktree created, entity not yet persisted) or
  a failed-and-swallowed `worktree.remove` mid-`kill_entity` leaves an
  **orphan**: a directory under `WORKTREES_DIR` with no owning entity.
  Nothing sweeps these today; they accumulate invisibly.

A startup reconciliation must re-adopt and sweep. The danger is the sweep:
on the live host, `git worktree list` returns the main checkout **plus the
developer's own Claude Code sessions under `.claude/worktrees/`** (5 live at
design time, including in-flight ticket work). Claude Code's own Workflow
leaf worktrees (`isolation:'worktree'`) also land under `.claude/worktrees/`
— indistinguishable by path from the developer's sessions. A sweep that
removed "any worktree with no matching Hive entity" would destroy active
human work.

## Decision

**1. Scope every sweep operation strictly to `WORKTREES_DIR`.** The
reconciliation filters `git worktree list` to children of `WORKTREES_DIR`
and ignores everything else. The main checkout and all `.claude/worktrees/`
worktrees (developer sessions *and* CC leaf-agent worktrees) are
structurally unreachable by the sweep — not by a denylist, but by being
outside the only directory it ever considers.

**2. Never delete a worktree that holds uncommitted work.** Orphans are
disposed by state: stale git-admin records → `git worktree prune`; clean
orphan dirs → remove; **dirty orphan dirs → audit + warn + leave in place**
(quarantine) for a human to resolve.

**3. Re-adoption derives the path from the entity name** (`WORKTREES_DIR /
name`), not a persisted column — the name is the single source of truth.

**4. CC leaf-agent worktree cleanup is out of scope** for Hive's sweep
(it cannot be done safely by path). It stays lead-JD discipline (016).

## Consequences

- The sweep is incapable of touching the developer's worktrees or the main
  checkout — the catastrophic-deletion class is closed by construction, not
  guarded against.
- Orphans with uncommitted work are never silently destroyed; the trade-off
  is that dirty orphans persist until a human clears them (surfaced via
  audit + log, so they are not invisible).
- Stranded CC leaf worktrees under `.claude/worktrees/` are not reclaimed by
  Hive; a forgetful lead can leak them. Accepted — the alternative risks
  human work, and 016's lead discipline is the right owner.
- No schema change: `worktree_path` stays `None` in the DB; re-adoption is
  name-derived. If a future need makes the path non-derivable, this ADR is
  revisited.
- Reconciliation mirrors `reconcile_orphaned_gates` (ADR-adjacent precedent,
  Ticket 003 #27): conservative, audited, no-op when `worktree_mgr` is
  unwired.
