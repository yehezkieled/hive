# 023 — Activate the worktree floor: isolate leaf work from the live checkout (015 follow-up)

## What

Wire the `WorktreeManager` into the production `ProcessManager` so Ticket
015's **worktree floor actually runs**. Today the floor is dead code in
production: `__main__.py` never constructs or passes `worktree_mgr`, so every
`if self._mgr.worktree_mgr:` is false and **leads spawn in the live repo
checkout**, not an isolated worktree. Make leaf work genuinely isolated, and
land the closely-coupled gaps that block the same live path (see *Fold-in*).

## Why — confirmed live 2026-06-11

While verifying the 015 smoke test, three facts proved the floor is inert and
unsafe:

```
lead PID 3045164  →  /proc/.../cwd        →  /home/hezki/projects/hive   ← the LIVE main checkout
worktree_mgr in __main__.py               →  never constructed/passed
git worktree list                         →  no lead worktree exists
```

So a lead (and any non-isolated leaf agent it launches) writes into the
running service's own working tree. **"Don't PR" is not protection** — the
deployed service imports from `/home/hezki/projects/hive/src`, so an
uncommitted edit to `src/` changes the running service immediately, no merge
needed. 015's hermetic tests passed because they injected a fake
`WorktreeManager`; nothing tested the production *wiring* — exactly the gap a
live run exposes.

## Acceptance

- `__main__.py` constructs a `WorktreeManager` (repo + worktree dir from
  config) and passes it to `ProcessManager`; it is non-None in the live
  service.
- A spawned **lead's cwd is a dedicated worktree**, not the main checkout
  (`/proc/<pid>/cwd` ≠ the repo root); `git worktree list` shows it.
- Killing a lead removes its worktree; the idempotent re-attach (015) holds
  across kill/respawn.
- Leaf agents launched via the Lead's Workflow run land their edits in a
  worktree, **never** in the live `src/` — proven by a deployed
  maestro→lead→leaf turn that edits a repo file and leaves the main checkout
  clean.
- Nested-worktree behaviour holds as observed in 015 (sibling worktrees on
  fresh branches; no `'main' already used` collisions).
- `ruff` + `pytest -m "not integration"` green; a maestro turn completes
  end-to-end with isolated leaf work.

## Fold-in (the rest of "fix everything" — Q2)

These surfaced on the same live path and block the handshake from completing;
the owner asked to bundle them here:

1. **Lead↔maestro message delivery (routing).** The live run stalled because
   the maestro **never received the lead's proposal** — otter said verbatim
   *"I haven't received the lead's breakdown message in my queue yet."* This
   is the [021](../021-router-user-queue/) routing gap, but **wider than
   maestro→user**: a *lead→maestro* peer message also failed to deliver.
   Either widen 021 to cover peer delivery, or fix here and cross-link. The
   floor alone is useless if the org can't pass messages.
2. **Hardcoded default-fallback audit.** The opus-default fix
   ([PR #90](https://github.com/yehezkieled/hive/pull/90)) found a
   `model = action.model or "sonnet"` in `message_dispatcher` and a `sonnet`
   facade default sitting *above* the canonical `create_team` default and
   silently overriding it. Sweep the spawn paths for other "hardcoded default
   above the real default" layers (cwd, permission_mode, advisor, model) and
   collapse them to one source of truth.

## Non-goals

- The Workflow engine itself (015 — done) and the `spawn_worker` migration
  (016).
- Per-agent worktree cleanup of *changed* leaf worktrees (016's scope).
- The advisor flag / opus-model fixes (already shipped: PRs #89, #90).

## Notes

Found during the 015 live smoke test. **Blocks 015's live definition of
done** (a maestro turn completing end-to-end with isolated leaf work), so it
ranks above the other S6 candidates (019–022). Pairs with
[020](../020-adapter-liveness-escalation/) (recovery) and
[021](../021-router-user-queue/) (routing).
