---
id: T014
title: Bounce/idle guard bundle
epic: E06
milestone: M2
status: todo
priority: P2
depends_on: []
owner:
auto: no
plan: none
ready: no
issue: 281
pr:
---
## What
A bundle of the concrete false-fire fixes from the 2026-06-30 sweep: exempt `awaiting_decision` entities from the idle reaper (today a parked non-maestro is auto-killed at 30 min, unrecoverable); set a waiting flag on lead → maestro escalation so a waiting lead is bounce-exempt (today only maestro → user sets it); widen or fail-safe the `workflow_active` liveness window (180s equals the reader timeout, so a slow-but-live Workflow can read inactive at timeout); bounded autonomous recovery from ERROR after give-up.

## Why
On a multi-day unattended run these known gaps false-kill or false-bounce legitimately waiting entities, or strand one permanently in ERROR.

## Acceptance
- [ ] An entity parked on `awaiting_decision` is never idle-reaped.
- [ ] A lead waiting on its maestro is bounce-exempt.
- [ ] A live Workflow run is never read as inactive because of the 180s window.
- [ ] An ERROR'd entity gets a bounded number of autonomous recovery attempts.

## Subtasks
- [ ] Idle-reaper exemption
- [ ] Lead waiting flag
- [ ] Liveness window
- [ ] ERROR recovery

## Plan
Approach:
Touches:
Tests first:
Decisions to record:
approved: no

## Notes
Migrated from `docs/archive/tickets/060-bounce-idle-guards/`. Acceptance drafted from the four listed gaps; settle at /pm:grill.

## Proposed changes
