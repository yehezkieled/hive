# 056 — Per-project worktree floor

> **Backlog — re-grill when scheduled into a sprint.** Roadmap Phase 6.
> **The file-isolation blocker** for building an external project on Hive.

## What

Leads build in **their project's own repo**, not Hive's. Replace the single
`WorktreeManager(PROJECT_ROOT=hive, WORKTREES_DIR)` with one resolved from the
lead's owning maestro's `Project.root_path` (repo = project root; worktree dir
under the project). Touches `bootstrap.py`, `lifecycle_manager`, `worktree.py`.

## Why

Today a project maestro is homed + fenced into its project, but its leads get
worktrees cut from **Hive's** repo on branch `hive/<lead>` — and the maestro's
ownership guard then blocks those leaf writes. So the maestro→lead→leaf build
**cannot complete** against an external repo. This is the load-bearing dogfood
blocker (found in the 2026-06-30 research sweep).

## Acceptance

TBD at grilling. (Likely wants an ADR; pairs with per-project crash-recovery.)
