# Plan — Ticket 025: Worktree crash-recovery  (issue #146)

DIRECT lane — one PR. Approach in [`design.md`](design.md); seam map +
test matrix in [`outline.md`](outline.md); decision in
[ADR 0015](../../adr/0015-worktree-reconciliation-scope-and-orphan-policy.md);
crash-point matrix + safety evidence in [`research.md`](research.md).

**Dependency:** 023 (worktree floor live) — done. No blockers.

## Files this Ticket creates / modifies

| Path | Op | Step |
|------|----|------|
| `src/hive/process/worktree.py` | modify | `managed_worktrees()` (filter to `WORKTREES_DIR`), `is_dirty(name)`, `prune()` |
| `src/hive/process/lifecycle_manager.py` | modify | `reconcile_worktrees()` — eager re-adopt loop + orphan sweep, audited |
| `src/hive/process/manager.py` | modify | facade `reconcile_worktrees()` thin-delegate (mirrors `reconcile_orphaned_gates`) |
| `src/hive/__main__.py` | modify | one `await process_manager.reconcile_worktrees()` after `rebuild_hierarchy()` |
| `tests/process/test_worktree.py` | modify | git-side helpers + safety/orphan tests (real `git` in `tmp_path`) |
| `tests/process/test_lifecycle_manager.py` | modify | re-adopt entity-state tests; no-op when `worktree_mgr is None` |

## Verification

- `ruff check src/ tests/ && ruff format --check src/ tests/` (separate gates).
- `pytest -m "not integration"` green (75% coverage floor).
- Crash-point matrix passes (outline test table), incl. the load-bearing
  **out-of-scope-untouched** safety test (a worktree outside `WORKTREES_DIR`
  is never touched).
- Live re-smoke (deployed, S6 DoD): crash mid-task with a dirty lead
  worktree → restart → lead re-adopts (`/proc/<pid>/cwd` = worktree, edits
  intact); a planted clean orphan is swept, a planted dirty orphan is
  quarantined; no `.claude/worktrees/` session touched.

## Out of scope

- Sweeping CC leaf-agent worktrees under `.claude/worktrees/` (unsafe by
  path — lead-JD discipline, 016).
- Persisting `worktree_path` to the DB (derive-from-name supersedes).
- Auto-bounce / healing of jammed sessions (020).

## Cross-cutting impact

- **ADR:** 0015 (created, committed with `design.md`).
- **Glossary:** `Worktree reconciliation` + `Orphan worktree` added to
  `CONTEXT.md` (committed with `design.md`).
- **Reference docs:** none — `DEPLOYMENT.md` unaffected (no deploy-procedure
  change; reconciliation is automatic at startup).
- **INDEX:** row carries issue #146; flip to *done* when the PR merges and
  the live re-smoke passes.

## Build

One branch, one PR that closes #146 (you or a single agent). Apply the
seams in order s1 → s2 → s3 (outline), tests alongside.
