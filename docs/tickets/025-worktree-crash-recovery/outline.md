# 025 — Outline (implementation structure)

Approach in [`design.md`](design.md); decision in
[ADR 0016](../../adr/0016-worktree-reconciliation-scope-and-orphan-policy.md).
One cohesive change (DIRECT lane) across three seams + tests.

## Seam map

```
s1 GIT-SIDE          WorktreeManager gains list/classify/prune/is_dirty
   (WorktreeManager)   helpers, all scoped to WORKTREES_DIR

s2 RECONCILE          LifecycleManager.reconcile_worktrees():
   (LifecycleManager)  eager re-adopt loop + orphan sweep, audited;
                       facade thin-delegates

s3 STARTUP WIRING    __main__ calls reconcile after rebuild_hierarchy,
   (__main__)          next to reconcile_orphaned_gates
```

## s1 — `WorktreeManager` git helpers (`process/worktree.py`)

Builds on the existing `list_worktrees()` (`:93`). New methods, all
`WORKTREES_DIR`-bounded:

- `managed_worktrees() -> list[dict]` — `list_worktrees()` filtered to
  paths whose parent is `self.worktree_dir`. The single choke point that
  enforces the ADR 0016 scope; everything downstream consumes only this.
- `is_dirty(name) -> bool` — `git status --porcelain` in
  `worktree_dir/name`; truthy output ⇒ dirty (uncommitted work).
- `prune() -> list[str]` — `git worktree prune -v`; returns pruned paths
  (stale git-admin records, matrix #5).
- `remove(name)` — already exists (`:68`); reused for clean-orphan reclaim.

## s2 — `reconcile_worktrees()` (`process/lifecycle_manager.py`)

New method on `LifecycleManager`; facade `ProcessManager.reconcile_worktrees()`
thin-delegates (mirrors `reconcile_orphaned_gates`, `manager.py:445`).
No-op (`return empty report`) when `self._mgr.worktree_mgr is None`.

```
report = {readopted: [], pruned: [], removed: [], quarantined: []}

1. prune()                         → report.pruned        (safe first)
2. known = {WORKTREES_DIR/name for name in _entities if isinstance TeamLead}
3. RE-ADOPT: for each restored TeamLead with worktree_path is None:
     entity.worktree_path = await worktree_mgr.create(name, branch=hive/name)
     audit "worktree.readopted"   → report.readopted
4. SWEEP: for wt in worktree_mgr.managed_worktrees():
     if wt.path in known: continue                 # owned — skip
     if worktree_mgr.is_dirty(basename):           # orphan + dirty
         audit "worktree.orphan_quarantined"; logger.warning
         report.quarantined
     else:                                          # orphan + clean
         await worktree_mgr.remove(basename)
         audit "worktree.orphan_removed"; report.removed
logger.info one-line summary; return report
```

Re-adopt uses the idempotent `create()` — existing dir returned untouched
(edits intact, `worktree.py:27-29`); a missing dir is re-created fresh.
The re-adopt loop runs **before** the sweep so a just-adopted path is in
`known` and never mistaken for an orphan.

## s3 — Startup wiring (`__main__.py`)

One line after `rebuild_hierarchy()` (~line 284), beside the gate
reconciliation:

```python
process_manager.rebuild_hierarchy()
await process_manager.reconcile_worktrees()        # Ticket 025
await process_manager.reconcile_orphaned_gates()   # Ticket 003 #27
```

## Tests (`tests/process/test_worktree.py` + `test_lifecycle_manager.py`)

Reuse the real-`git`-in-`tmp_path` `repo` fixture (`test_worktree.py:25-39`)
— mocking git would test nothing. Crash-point matrix:

| Test | Setup | Assert |
|------|-------|--------|
| re-adopt clean (#1) | worktree + restored lead, dir survives | `worktree_path` set; dir + branch unchanged |
| re-adopt preserves edits | worktree with uncommitted edit | edit still present after reconcile |
| mid-spawn orphan, clean (#2) | worktree dir, **no** entity, clean | removed; audited `orphan_removed` |
| mid-kill orphan, dirty (#3) | orphan dir with uncommitted work | **kept**; audited `orphan_quarantined` + warn |
| stale git-admin (#5) | worktree dir deleted out-of-band | `prune` clears the record |
| **safety: out-of-scope untouched** | a worktree **outside** `WORKTREES_DIR` (sibling dir) with no entity | **never touched** by reconcile |
| no-op | `worktree_mgr is None` | empty report, no error |

The safety test is the load-bearing one — it proves ADR 0016's scope by
construction.

## Verification gate

`ruff check src/ tests/ && ruff format --check src/ tests/` (separate
gates) + `pytest -m "not integration"` (75% floor).

Live re-smoke (deployed, per S6 DoD): crash Hive mid-task with a dirty lead
worktree → restart → confirm the lead re-adopts (`/proc/<pid>/cwd` =
worktree, edits intact) and a planted clean orphan is swept while a planted
dirty orphan is quarantined — with no `.claude/worktrees/` session touched.
