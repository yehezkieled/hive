---
id: T001
title: Unified needs-you lane
epic: E01
milestone: M1
status: todo
priority: P1
depends_on: []
owner:
auto: no
plan: none
ready: no
issue: 268
pr:
---
## What
Design, in the Claude design app, the one actionable feed + lane component that folds the four scattered "needs-you" interrupts into one: decision requests (029/038), mode-elevation approvals, vault payment approvals, interactive gates (003), plus blocked/errored loops. Today these live in 2 header bells + 3 separate SSE bubble types with copy-pasted approve/deny logic. The deliverable is an approved mockup of one `needs_you` feed rendered by one lane component, each item showing entity, kind, prompt/summary, and its inline action (reply field or approve/deny), reused by the Stack home hero (T002) and the Work view (T003). Implementation is T004.

## Why
"Which run needs me" is the scarcest resource (039). Four surfaces for one job is confusing and duplicated. One lane is the supervise-by-exception core of the Delegator's Desk, and it kills the approve/deny code copied across bells and bubbles.

## Acceptance
- [ ] An approved mockup shows one lane listing decision + mode + vault + gate + errored items, each with entity, kind, summary, and inline action.
- [ ] The mockup shows the calm "✓ all clear" empty state.
- [ ] The lane is designed so the same component serves the home hero and the Work view.
- [ ] The approved design is exported and stored at the mockups' agreed home (T005 decides where).

## Subtasks
- [ ] Inventory the four current surfaces (bells + bubbles) and their actions
- [ ] Draft the lane and empty state in the Claude design app
- [ ] Review on the iPad, iterate, mark approved

## Plan
Approach:
Touches:
Tests first:
Decisions to record:
approved: no

## Notes
Migrated from `docs/archive/tickets/051-unified-needs-you-lane/`. This is a design-app ticket: the deliverable is an approved mockup, not code, so it is closed by hand when the design is approved rather than by /pm:work. Non-goals: new approval types, the home layout (T002), push delivery (already 041).

## Proposed changes
