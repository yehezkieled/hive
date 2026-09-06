---
id: T003
title: Tabbed Work view
epic: E01
milestone: M1
status: todo
priority: P1
depends_on: [T002]
owner:
auto: no
plan: none
ready: no
issue: 270
pr:
---
## What
Design, in the Claude design app, a tabbed, conversation-rich workspace for actively working with 2–3 maestros: one tab per maestro/project (tapping a home project opens or focuses its tab; a "+" opens more); the real conversation thread with decisions surfacing inline and markdown/tables rendering; the active tab IS the default target (no `/m:` needed; the composer reads "messages go to <maestro>"); a dedicated Clear button with an anchored popover (Clear view keeps the maestro's memory / Clear + reset memory resets the session / Cancel); a dedicated History button; and a compact loop-status header that stays visible. Absorbs 049 (deep-link reply target). Implementation is T004.

## Why
The Delegator's Desk needs an active-work mode: converse with a few maestros, switch fast, without re-addressing every message. Folds in the deep-link reply target (049) and the "erase chat + history + default target" asks.

## Acceptance
- [ ] An approved mockup shows tabs across 2–3 maestros with the active tab as default target.
- [ ] The mockup shows the Clear popover (view-only vs +reset) and the History control.
- [ ] The mockup shows how a needs-you push deep-link lands on the right tab, reply-ready.
- [ ] The approved design is exported and stored at the mockups' agreed home.

## Subtasks
- [ ] Draft the tab bar, composer target line, and loop-status header
- [ ] Design the Clear popover and History
- [ ] Review on the iPad, iterate, mark approved

## Plan
Approach:
Touches:
Tests first:
Decisions to record:
approved: no

## Notes
Migrated from `docs/archive/tickets/053-tabbed-work-view/`. Design-app ticket, closed by hand on approval. Non-goals: the home (T002), steering a live Workflow run (ADR 0014), per-entity controls beyond clear/reset.

## Proposed changes
