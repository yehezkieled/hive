---
id: T011
title: Create-project + maestro flow
epic: E05
milestone: M2
status: todo
priority: P2
depends_on: []
owner:
auto: no
plan: none
ready: no
issue: 278
pr:
---
## What
One guided, atomic flow to register a project at a validated git repo path and create/assign its non-PA maestro together, with sane unattended defaults. Collapses today's manual, never-linked `/new maestro` + `/project new|assign`. May surface on the web once the Desk lands (M1 only displays projects).

## Why
Today creating a project maestro is a two-step CLI dance with no validation that the path is a git work-tree or that the maestro is not the PA, so a mis-wired project (maestro homed with no git; leads silently using Hive's repo) is likely.

## Acceptance
- [ ] One command or web flow registers a project and its maestro atomically, rejecting a non-git path and the PA maestro.
- [ ] The old two-step path is removed or aliased to the new flow; help and drift tests updated.

## Subtasks
- [ ] Define the flow and its validations
- [ ] Implement command + tests
- [ ] Decide whether it gets a web surface

## Plan
Approach:
Touches:
Tests first:
Decisions to record:
approved: no

## Notes
Migrated from `docs/archive/tickets/058-create-project-flow/`. Acceptance is a first draft; settle at /pm:grill.

## Proposed changes
