---
id: T012
title: Dogfood kickoff + smoke (finance app on Hive)
epic: E05
milestone: M2
status: todo
priority: P2
depends_on: [T009, T010, T011]
owner:
auto: no
plan: none
ready: no
issue: 279
pr:
---
## What
Stand up the finance-app project on Hive and prove maestro → lead → leaf end to end on the external repo: a project maestro homed in the finance-app repo spawns a lead whose worktree is in that repo, a leaf edits a file there, the ownership guard allows it, and Hive's own checkout stays clean. Then kick off the real build.

## Why
The deployed acceptance that isolation, the per-project worktree floor, and the create flow actually let a real product build run on Hive, the multi-repo analogue of ticket 023's live definition of done. The build is the forcing function that generates the rest of the hardening backlog.

## Acceptance
- [ ] On the deployed host, a finance-app maestro's lead completes a leaf edit inside the finance-app repo with Hive's checkout untouched (`git status` clean).
- [ ] The first real build goal is delegated from the Desk and the friction found is filed as tickets.

## Subtasks
- [ ] Register the finance app via T011
- [ ] Run the smoke
- [ ] Delegate the first goal and file what breaks

## Plan
Approach:
Touches:
Tests first:
Decisions to record:
approved: no

## Notes
Migrated from `docs/archive/tickets/063-dogfood-kickoff-smoke/`. Acceptance is a first draft; settle at /pm:grill.

## Proposed changes
