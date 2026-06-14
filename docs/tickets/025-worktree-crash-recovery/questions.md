# 025 — Questions (the unknowns going in)

Seeded by [`ticket.md`](ticket.md). 025 is the crash-recovery follow-up
to [`023`](../023-activate-worktree-floor/) (the floor is now live). These
are the things to settle before designing the startup reconciliation.

## Re-adoption

- **Q1 — Coverage.** Does the lazy idempotent `create()` path (023's
  `_get_or_create_adapter`) actually re-adopt a worktree across *every*
  crash point, or only the clean normal-restart case? Where does it
  silently *not* fire?
- **Q2 — Persistence.** Is a lead's `worktree_path` round-tripped through
  the entity store, so a restored lead even knows where its worktree is?
  If not, how is the path recovered — derived from the name, or
  re-persisted?
- **Q3 — Edit preservation.** When a worktree is re-adopted, are the
  branch checkout and uncommitted edits genuinely preserved, or does
  re-creation reset them?

## Crash-point matrix

- **Q4 — The matrix.** Enumerate the crash points (mid-spawn, mid-kill,
  normal restart, partial cleanup) and classify each: re-adopt, orphan,
  or benign? Which orderings in `create_team` / `kill_entity` actually
  produce an orphan?

## Startup wiring

- **Q5 — Hook point + precedent.** Where in the startup sequence does
  reconciliation belong, and is there an existing pattern to mirror (e.g.
  the orphaned-gate reconciliation)?
- **Q6 — Home + return.** Where should the reconcile logic live —
  `WorktreeManager` (owns git ops), `LifecycleManager` (owns entity
  state), or the facade? What does it return / audit?

## Safety boundary (the dangerous part)

- **Q7 — What worktrees exist live.** What does `git worktree list`
  actually return on the host, and how do we tell a Hive-managed lead
  worktree apart from the developer's own `.claude/worktrees/` sessions
  and the main checkout?
- **Q8 — CC leaf worktrees.** Do Claude Code Workflow leaf-agent
  worktrees (`isolation: 'worktree'`) land under Hive's `WORKTREES_DIR`
  or somewhere the sweep must *not* touch? Can the candidate "sweep
  stranded leaf siblings" scope (ticket Non-goals §2) be done safely?

## Orphan policy

- **Q9 — Policy.** For a true orphan: log-only, remove, or quarantine?
  What happens when an orphan still holds **uncommitted work** — destroy
  it, or preserve it? (Echoes the project's "unpushed commits go
  dangling" footgun.)

## Tests

- **Q10 — Hermetic coverage.** What test patterns already exist for
  worktree behaviour, and can the crash-point matrix run hermetically
  (real `git` against a throwaway repo, no Postgres)?
