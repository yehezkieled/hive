---
id: T009
title: Per-project worktree floor
epic: E04
milestone: M2
status: todo
priority: P1
depends_on: [T008]
owner:
auto: no
plan: required
ready: no
issue: 276
pr:
---
## What
Leads build in their project's own repo, not Hive's. Replace the single `WorktreeManager(PROJECT_ROOT=hive, WORKTREES_DIR)` with one resolved from the lead's owning maestro's `Project.root_path` (repo = project root; worktree dir under the project). Touches `bootstrap.py`, `lifecycle_manager`, `worktree.py`. Likely wants an ADR and pairs with per-project crash recovery (worktree reconciliation, ADR 0016).

## Why
Today a project maestro is homed and fenced into its project, but its leads get worktrees cut from Hive's repo on branch `hive/<lead>`, and the maestro's ownership guard then blocks those leaf writes. So the maestro → lead → leaf build cannot complete against an external repo. This is the load-bearing dogfood blocker (2026-06-30 research sweep).

## Acceptance
- [ ] A lead under a project maestro gets its worktree cut from the project's repo, and a leaf edit there passes the ownership guard.
- [ ] Worktree reconciliation on startup re-adopts and sweeps per-project worktrees with the same guarantees as today (never touches the main checkout, never deletes uncommitted work).

## Subtasks
- [ ] Resolve the worktree manager per project
- [ ] Extend reconciliation
- [ ] ADR + tests

## Plan
Approach:
Touches:
Tests first:
Decisions to record:
approved: no

## Notes
Migrated from `docs/archive/tickets/056-per-project-worktree-floor/`. Acceptance lines are a first draft; settle them at /pm:grill after T008's model.

## Proposed changes
