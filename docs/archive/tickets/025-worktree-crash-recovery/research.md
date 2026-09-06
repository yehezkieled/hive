# 025 — Research

Answers to [`questions.md`](questions.md), grounded in the code on this
host, the live `git worktree list`, and the 023 artifacts. Researched
2026-06-14. Line refs are against `main` at the time of writing.

## Headline findings

1. **`worktree_path` is never persisted.** `entity_store.py:74` hardcodes
   `None` into the `worktree_path` column on every upsert ("was
   Worker-only; Workers retired, Ticket 018"), and `_row_to_entity`
   doesn't read the column back. So **every restored lead comes back
   path-less** — re-adoption must *derive* the path from the entity name,
   not read it from the DB. (Confirms the 023 comment at
   `lifecycle_manager.py:253-257`.)
2. **Lazy re-adoption already works for the clean case — but only that
   case.** `_get_or_create_adapter` re-provisions a path-less lead via the
   idempotent `WorktreeManager.create()`, which returns the surviving dir
   untouched (`worktree.py:27-29`) — branch and edits intact. The gap 025
   fills is the *other* crash points (orphans) and making re-adoption
   **eager + audited** instead of a silent lazy side-effect.
3. **The orphan sweep is genuinely new — and dangerous.** Nothing sweeps
   today (confirms 023 research Q4). The live `git worktree list` shows
   the main checkout **plus 5 active `.claude/worktrees/` dev sessions**
   while Hive's `WORKTREES_DIR` is empty — so a naive "remove any worktree
   without an entity" sweep would **delete the developer's own in-flight
   work**. Scoping the sweep strictly to `WORKTREES_DIR` is the load-
   bearing safety constraint of this ticket.
4. **There is a clean precedent to mirror.** `reconcile_orphaned_gates()`
   (`approval_handler.py:540`) is a startup reconciliation that is
   conservative by construction: no-op when its store is missing, never
   destructive-by-default, leaves live state untouched, returns the rows
   it touched. 025's `reconcile_worktrees()` should be the same shape.

## Q1–Q3 — Re-adoption

**The mechanism (already present, lazy).** A restored `TeamLead` comes
back with `worktree_path=None` (Q2 below). On its next turn,
`_get_or_create_adapter` (`lifecycle_manager.py:258-261`) hits:

```python
if isinstance(entity, TeamLead) and entity.worktree_path is None and self._mgr.worktree_mgr:
    entity.worktree_path = await self._mgr.worktree_mgr.create(
        entity.name, branch=f"hive/{entity.name}"
    )
```

`create()` (`worktree.py:25-29`): if `WORKTREES_DIR/<name>` exists on
disk, it is **returned as-is with no git command run** — so the branch
checkout and any uncommitted edits are preserved (the directory is never
touched). The path and branch are fully deterministic from the name
(`WORKTREES_DIR/<name>`, branch `hive/<name>`), set identically at
`create_team` (`lifecycle_manager.py:314-317`).

- **Q1 (coverage):** the lazy path re-adopts correctly **for a restored
  lead whose worktree dir survived** (the normal-restart case). It does
  **not** help the two orphan cases (Q4) — there is no entity to drive the
  lazy call. It is also **deferred and silent**: nothing happens until the
  lead's next turn, and there's no audit/log that a re-adoption occurred.
- **Q2 (persistence):** **not persisted.** `entity_store.py:74` writes
  `None`; `_row_to_entity` (`:122-158`) never sets `worktree_path`, so
  `TeamLead`'s dataclass default (`team_lead.py:29`, `None`) wins. Path
  recovery is **derive-from-name**, not DB round-trip.
- **Q3 (edits):** preserved — the existing-dir branch of `create()` is a
  pure return, no checkout/reset.

## Q4 — Crash-point matrix

Tracing `create_team` (`lifecycle_manager.py:281-349`) and `kill_entity`
(`:370-426`):

```
create_team order:   worktree.create  →  build entity  →  add to _entities
                     →  router.register  →  persist(lead)   [worktree_path
                                                             stored as None]

kill_entity order:   stop adapter  →  worktree.remove (try/except, SWALLOWS)
                     →  remove_team  →  pop _entities  →  entity_store.delete
```

| # | Crash point | DB row | Disk worktree | Classification | Handling |
|---|-------------|--------|---------------|----------------|----------|
| 1 | Normal restart | present | exists | **re-adopt** | eager set path (or lazy fallback); edits intact |
| 2 | Mid-spawn: crash between `worktree.create` and `persist` | absent | exists | **ORPHAN** | audit + policy (Q9) |
| 3 | Mid-kill: `worktree.remove` raises → swallowed (`:392`) → `delete` runs | absent | exists | **ORPHAN** | audit + policy (Q9) |
| 4 | Mid-kill: `remove` ok, crash before `delete` | present | gone | benign | lazy `create` re-makes fresh (edits already gone) |
| 5 | Mid-kill: `remove`'s `shutil` fallback (`:84-89`) deletes dir, git admin record dangles | varies | dir gone, git metadata stale | git-level orphan | `git worktree prune` |
| 6 | Normal restart, dir manually deleted | present | gone | benign | lazy `create` re-makes fresh |

The two real orphans are **#2 (mid-spawn)** and **#3 (mid-kill remove
failed)**. Both leave a `WORKTREES_DIR/<name>` directory with no owning
entity. #5 is the git-admin variant, cured by `prune`.

## Q5–Q6 — Startup wiring & home

- **Q5 (hook point):** `__main__.py:277-287` runs
  `restore()` → `rebuild_hierarchy()` → `reconcile_orphaned_gates()`.
  The worktree reconciliation slots in **right here**, after
  `rebuild_hierarchy()` (so the full lead set is known) — directly
  alongside the gate reconciliation. One new line:
  `await process_manager.reconcile_worktrees()`.
- **Precedent:** `reconcile_orphaned_gates` (`approval_handler.py:540-587`)
  — guard on missing store (`return []`), iterate, skip live ones, audit
  each (`"gate.reconcile_stale"`), `logger.info` a count, return rows.
- **Q6 (home):** split by concern, mirroring the facade→collaborator
  pattern (`manager.py:445-446`):
  - `WorktreeManager` already has `list_worktrees()` (`worktree.py:93`) —
    add the **git-side** logic here (list, filter to `WORKTREES_DIR`,
    classify, prune/remove).
  - `LifecycleManager` owns **entity-state** mutation (setting
    `worktree_path` on restored leads) and has `_entities` via `_mgr`.
  - Facade `reconcile_worktrees()` delegates to a `LifecycleManager`
    method that drives both halves and audits. Returns a small report
    (re-adopted names + orphan dispositions) like the gate precedent.

## Q7–Q8 — Safety boundary (the dangerous part)

Live `git worktree list --porcelain` on the host **right now**:

```
/home/hezki/projects/hive                                      [main]          ← MAIN checkout
/home/hezki/projects/hive/.claude/worktrees/flickering-...     [worktree-...]  ← this session
/home/hezki/projects/hive/.claude/worktrees/graceful-...       [ticket-029/...] ← LIVE 029 work
/home/hezki/projects/hive/.claude/worktrees/inherited-...      [worktree-...]
/home/hezki/projects/hive/.claude/worktrees/purrfect-...       [worktree-...]
/home/hezki/projects/hive/.claude/worktrees/snoopy-...         [s5-close-s6-open]
```

`WORKTREES_DIR` = `<repo>/worktrees/` (`config.py:58`) and is **empty**.

- **Q7 (distinguishing):** Hive-managed lead worktrees live **only** under
  `WORKTREES_DIR` (`<repo>/worktrees/<maestro>.<team>`, branch
  `hive/<...>`). The dev's Claude Code sessions live under
  `<repo>/.claude/worktrees/` — a **different directory**. The main
  checkout is the repo root. **Filtering `git worktree list` to children
  of `WORKTREES_DIR` cleanly isolates Hive's worktrees** from everything
  the sweep must never touch. A sweep that keyed off "no matching entity"
  *without* this filter would delete all 5 dev sessions (including the
  active ticket-029 research) and choke on main — catastrophic.
- **Q8 (CC leaf worktrees):** Claude Code's Workflow `isolation:
  'worktree'` creates worktrees under the **`.claude/worktrees/`** tree,
  **not** `WORKTREES_DIR` — the same place the dev's own sessions live.
  So Hive **cannot** safely sweep stranded leaf worktrees by location:
  there's no path-based way to tell a forgotten leaf worktree from the
  developer's active one. **Recommendation: drop the ticket's candidate
  "sweep leaf siblings" scope (Non-goals §2).** Leaf-worktree cleanup
  stays lead-JD discipline (016 design D5). 025's sweep is
  `WORKTREES_DIR`-only. `CONFIRM IN CODE`: the exact CC leaf-worktree dir
  during implementation, but it is demonstrably *not* `WORKTREES_DIR`,
  which is all the safety argument needs.

## Q9 — Orphan policy

The ticket says orphans must be "removed or adopted per a deliberate
policy, not left to accumulate silently." Given the safety stakes and the
project's "unpushed commits go dangling" footgun, the conservative,
deliberate policy:

- **git-admin-only stale entries (matrix #5):** `git worktree prune` —
  safe, removes nothing on disk.
- **Orphan dir, clean (no uncommitted work):** audit + `remove()`. Safe to
  reclaim; nothing to lose.
- **Orphan dir, dirty (uncommitted work):** **do not delete** — audit +
  `logger.warning` + leave in place (quarantine), surfaced for a human.
  Destroying unpushed work silently is the exact failure mode to avoid.

This is a *design fork* for the grill — the alternative is uniform
log-only (never auto-remove anything), which is safer still but lets clean
orphans accumulate. (See `design.md`.)

## Q10 — Tests (hermetic, already patterned)

`tests/process/test_worktree.py` drives a **real `git`** against a
throwaway repo in `tmp_path` with a real `WorktreeManager` (fixture
`repo`, lines 25-39) — "the manager is a thin subprocess wrapper, so
mocking git would test nothing." The crash-point matrix tests reuse this
exactly:

- Re-adopt (#1): create worktree + dirty edit → new `WorktreeManager` over
  same dir → reconcile → path re-adopted, edit still present.
- Mid-spawn orphan (#2): create worktree, **no** entity → reconcile →
  audited + handled per policy.
- Mid-kill orphan (#3): create worktree, simulate `remove` failure →
  reconcile picks it up.
- Safety: a worktree **outside** `WORKTREES_DIR` (e.g. a `.claude/`-style
  sibling) is **never** touched by reconcile.
- Dirty-orphan: orphan with uncommitted work is preserved, not deleted.

`tests/process/test_lifecycle_manager.py` covers the entity-state half
(restored lead → `worktree_path` set eagerly). `tests/test_bootstrap.py`
already asserts the production composition wires a real `WorktreeManager`,
so reconcile gets a real manager in the live path. All hermetic, all under
`pytest -m "not integration"`.

## Lane call (provisional)

**DIRECT.** One cohesive feature — a single `reconcile_worktrees` method,
one startup line, audit events, and the crash-point matrix tests, touching
~3 files + tests. It doesn't break into 2+ independently-shippable PRs
worth fleet overhead (unlike 023's four seams). Finalised in `plan.md`.
