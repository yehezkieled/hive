# 025 — Design (chosen approach)

Seeded by [`ticket.md`](ticket.md); grounded in [`research.md`](research.md);
grilled 2026-06-14 (owner approved A–E). Safety-boundary + orphan-policy
decision recorded in
[ADR 0016](../../adr/0016-worktree-reconciliation-scope-and-orphan-policy.md).

## Decision in one line

Add a single startup pass — `reconcile_worktrees()`, run right after
`rebuild_hierarchy()` next to the existing gate reconciliation — that
**eagerly re-adopts** each restored lead's worktree (path derived from its
name, edits intact) and **sweeps orphans**, with the sweep scoped strictly
to `WORKTREES_DIR` and refusing to delete any worktree that holds
uncommitted work.

## The five decisions

### A — Re-adopt eagerly + audited (not lazy-only)

023's lazy path (`_get_or_create_adapter`, `lifecycle_manager.py:258-261`)
already re-adopts a restored lead's worktree functionally — but only on the
lead's *next turn*, silently, and only for the clean normal-restart case.
025 makes it **eager**: at startup, for every restored `TeamLead`, set
`entity.worktree_path` to its derived path and ensure the worktree exists
(idempotent `create()` — returns the surviving dir untouched, edits
preserved). This makes re-adoption observable (one audit event per lead),
verifies edit-preservation at boot rather than trusting it lazily, and
gives a single place that also sees the orphan cases the lazy path never
fires for. The lazy path stays as the fallback for any lead created after
startup.

### B — Derive the path from the name (no DB round-trip)

`worktree_path` is **not persisted**: `entity_store.py:74` hardcodes `None`
(an 018 Worker-retirement leftover) and `_row_to_entity` never reads it
back. Rather than re-add a column + migration, derive the path the same way
every other call site already does: `WORKTREES_DIR / name`, branch
`hive/<name>` (`lifecycle_manager.py:314-317`, `:258-261`). The entity name
is the single source of truth; persisting the path would just be a second,
drift-prone copy.

### C — Smart orphan policy (prune / remove-clean / quarantine-dirty)

A true orphan is a directory under `WORKTREES_DIR` with no owning lead in
the restored set (crash matrix #2 mid-spawn, #3 failed-mid-kill — see
`research.md`). Disposition by state:

```
orphan in WORKTREES_DIR
   ├── git-admin record only, dir already gone → git worktree prune   [safe]
   ├── dir present, clean (no uncommitted work) → audit + remove()     [reclaim]
   └── dir present, dirty (uncommitted work)    → audit + WARN + keep   [quarantine]
```

"Dirty" = `git status --porcelain` in the worktree returns anything.
Quarantine never deletes — destroying unpushed work silently is the exact
failure mode to avoid (the project's "unpushed commits go dangling"
footgun). A dirty orphan is surfaced (audit `worktree.orphan_quarantined`
+ a `logger.warning`) for a human to resolve. Rejected: uniform log-only
(clean orphans accumulate) and uniform remove (nukes crash work).

### D — Sweep scoped to `WORKTREES_DIR` only

The sweep filters `git worktree list` to **children of `WORKTREES_DIR`**
and ignores everything else — the main checkout and the developer's own
`.claude/worktrees/` sessions are structurally out of reach. This is the
load-bearing safety constraint: at design time the host had the main
checkout plus 5 live `.claude/worktrees/` dev sessions (including active
ticket-029 work), and an unscoped "no matching entity ⇒ remove" sweep would
delete all of them.

**Consequence — the leaf-sweep candidate scope is dropped.** Claude Code
Workflow leaf worktrees (`isolation:'worktree'`) live under
`.claude/worktrees/`, indistinguishable by path from the developer's own
sessions, so Hive cannot safely sweep stranded leaf siblings. That cleanup
stays lead-JD discipline (016 design D5). Ticket Non-goals §2's "candidate
scope" is resolved as **out of scope**.

### E — ADR 0016

The `WORKTREES_DIR`-only boundary and the never-delete-dirty policy are
durable, cross-cutting rules that future worktree work must not violate, so
they get an ADR (mirroring 023's ADR 0011 for its pinning decision).

## Home & shape (mirrors `reconcile_orphaned_gates`)

Following the facade→collaborator pattern and the gate-reconciliation
precedent (`approval_handler.py:540-587`):

- **`WorktreeManager`** (owns git ops; already has `list_worktrees()`):
  gains the git-side helpers — list+filter to `WORKTREES_DIR`, classify a
  path (clean/dirty/stale), `prune()`, and `is_dirty(name)`.
- **`LifecycleManager`** (owns entity state, reaches `_entities` via
  `_mgr`): gains `reconcile_worktrees()` — eager re-adopt loop + orphan
  sweep, auditing each action; returns a small report
  (`{readopted: [...], pruned: [...], removed: [...], quarantined: [...]}`).
- **Facade** `ProcessManager.reconcile_worktrees()` thin-delegates.
- **`__main__.py`**: one new `await process_manager.reconcile_worktrees()`
  after `rebuild_hierarchy()` (line ~284), guarded no-op when
  `worktree_mgr is None`.

## Glossary / ADR impact

- **ADR 0016** created (this decision).
- **CONTEXT.md**: add **Worktree reconciliation** + **Orphan worktree** to
  the Execution section (new concepts the dashboard/logs will surface).

## Out of scope (restated)

- Sweeping CC leaf-agent worktrees under `.claude/worktrees/` (unsafe by
  path; lead-JD discipline, 016).
- Persisting `worktree_path` back to the DB (derive-from-name supersedes).
- Auto-bounce / healing of jammed sessions (020).
- Backup/restore of worktree contents beyond what git already keeps.
