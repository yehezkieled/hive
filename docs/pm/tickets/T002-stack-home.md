---
id: T002
title: Stack home
epic: E01
milestone: M1
status: todo
priority: P1
depends_on: [T001]
owner:
auto: no
plan: none
ready: no
issue: 269
pr:
---
## What
Design, in the Claude design app, the Delegator's Desk **Stack** home that replaces today's fleet-monitor landing: the needs-you lane (T001) as the hero (loud when non-empty, a calm "✓ all clear · N loops running" when empty); project-glance cards per project (loop status running / idle / blocked, what it is doing now, progress; tap opens the Work view, T003); an always-present delegate bar that gives the active project's maestro a goal in plain language and shows the target; and an ambient quota chip in the chrome. Principle: default calm, exceptions loud. Portrait-first iPad. Implementation is T004.

## Why
Today's landing is an unusable fleet-monitor. The Stack home is the delegate-and-supervise surface for autonomous loops, the actual daily driver (ADR 0027).

## Acceptance
- [ ] An approved mockup shows the home with needs-you hero, project cards, delegate bar, and quota chip, in portrait and landscape.
- [ ] The mockup shows the calm empty state and the loud non-empty state.
- [ ] Tapping a project card is designed to open or focus that project's Work-view tab.
- [ ] The approved design is exported and stored at the mockups' agreed home.

## Subtasks
- [ ] Draft the home layout around the lane from T001
- [ ] Design the project card states (running / idle / blocked) and the delegate bar target
- [ ] Review on the iPad, iterate, mark approved

## Plan
Approach:
Touches:
Tests first:
Decisions to record:
approved: no

## Notes
Migrated from `docs/archive/tickets/052-stack-home/`. Design-app ticket, closed by hand on approval. Non-goals: the Work view internals (T003), project create/management (T011), new observability widgets.

## Proposed changes
