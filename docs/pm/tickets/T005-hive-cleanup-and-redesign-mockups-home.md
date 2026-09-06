---
id: T005
title: Hive cleanup and redesign mockups home
epic: E03
milestone: M1
status: todo
priority: P2
depends_on: [T004]
owner:
auto: no
plan: none
ready: no
issue: 272
pr:
---
## What
A single housekeeping pass over the Hive project once the redesign has landed: decide the permanent home of the Delegator's Desk mockups (the 5 hand-coded brainstorm HTML files parked in `docs/archive/tickets/054-hive-cleanup/mockups/` plus the design-app exports from T001–T003) and act on it; remove `src/hive/web/static/brainstorm/` (mockups parked there so the iPad could reach them; they must not ship in the deployed app); remove or gitignore the `.superpowers/brainstorm/` scratch; prune merged `ticket-04x/*` and `ticket-05x/*` local branches and orphaned brainstorm worktrees under `.claude/worktrees/`; any doc drift surfaced during the redesign.

## Why
The redesign brainstorm left scratch where it should not live: HTML mockups inside the deployed `static/` dir, companion files under `.superpowers/`, and a pile of merged local branches. One pass, run last, sweeps it once the redesign's real artifacts are in place.

## Acceptance
- [ ] No redesign scratch is left under `src/hive/web/static/`.
- [ ] The mockups (hand-coded + design-app exports) sit at their agreed home, or are removed, and the decision is recorded in `docs/pm/decisions.md`.
- [ ] Merged `ticket-*` local branches are pruned; `.superpowers/` is gitignored or removed.
- [ ] Full check command green; no behaviour change.

## Subtasks
- [ ] Decide and record the mockups' home
- [ ] Remove `static/brainstorm/` and `.superpowers/`
- [ ] Prune branches and worktrees

## Plan
Approach:
Touches:
Tests first:
Decisions to record:
approved: no

## Notes
Migrated from `docs/archive/tickets/054-hive-cleanup/`. Run last in M1. Non-goals: the redesign build (T004), architecture-deepening refactors (Backlog).

## Proposed changes
