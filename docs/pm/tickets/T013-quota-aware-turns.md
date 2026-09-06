---
id: T013
title: Quota-aware turns
epic: E06
milestone: M2
status: todo
priority: P1
depends_on: []
owner:
auto: no
plan: required
ready: no
issue: 280
pr:
---
## What
Detect plan-quota exhaustion at the turn layer and surface it as a named outcome, not a generic stall: hold off the auto-bounce on a quota wall, gate the scheduler/dispatcher on `get_quota()` and pause until `resets_at` instead of flapping, and refresh the OAuth token so a multi-day run does not go blind.

## Why
At plan-quota 100% Claude Code writes no transcript entry, so every turn dead-ends in the 180s no-progress timeout, is misclassified as a stall, and the auto-bounce flaps the entity into ERROR with "cause unknown". A multi-day build will hit quota and die silently (2026-06-30 sweep). The number-one silent death for a long unattended build.

## Acceptance
- [ ] A turn that hits a quota wall ends with a distinct quota outcome, not a stall, and the entity is not bounced.
- [ ] The scheduler pauses dispatch until the quota window resets and resumes on its own.
- [ ] The OAuth token is refreshed across a multi-day run; a test covers expiry.

## Subtasks
- [ ] Name the quota outcome in the adapter
- [ ] Gate scheduler on quota
- [ ] Token refresh

## Plan
Approach:
Touches:
Tests first:
Decisions to record:
approved: no

## Notes
Migrated from `docs/archive/tickets/059-quota-aware-turns/`. Acceptance is a first draft; settle at /pm:grill.

## Proposed changes
