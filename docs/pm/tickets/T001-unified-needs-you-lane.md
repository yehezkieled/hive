---
id: T001
title: Unified needs-you lane
epic: E01
milestone: M1
status: in progress
priority: P1
depends_on: []
owner: work-t001
auto: no
plan: none
ready: yes
issue: 268
pr:
---
## What
Design, as an HTML mockup reviewed in Lavish Editor, the one actionable feed + lane component that folds the four scattered "needs-you" interrupts into one: decision requests (029/038), mode-elevation approvals, vault payment approvals, interactive gates (003), plus blocked/errored loops. Today these live in 2 header bells + 3 separate SSE bubble types in `src/hive/web/templates/landing.html` with copy-pasted approve/deny logic. The deliverable is the approved file `docs/design/T001-needs-you-lane.html`: one `needs_you` feed rendered by one lane component, each item showing entity, kind, prompt/summary, and its inline action (reply field or approve/deny), rendered at both sizes it must serve — the full-width Stack home hero (T002) and the compact Work-view strip (T003) — plus the calm empty state. It starts from the brainstorm draft `docs/archive/tickets/054-hive-cleanup/mockups/needs-you-lane.html`. Drafts live in the gitignored `.lavish/` scratch; the review runs over the Tailscale/MagicDNS link (never `lavish-axi share`). Implementation is T004.

## Why
"Which run needs me" is the scarcest resource (039). Four surfaces for one job is confusing and duplicated. One lane is the supervise-by-exception core of the Delegator's Desk, and it kills the approve/deny code copied across bells and bubbles.

## Acceptance
- [ ] `docs/design/T001-needs-you-lane.html` opens in a browser and shows one lane with five item kinds — decision request, mode-elevation approval, vault payment approval, interactive gate, errored/blocked loop — each with entity, kind, summary, and its inline action (reply field or approve/deny).
- [ ] The same file shows the calm empty state ("✓ all clear · N loops running").
- [ ] The lane is rendered twice in the file, full-width home hero and compact Work-view strip, from the same markup and CSS.
- [ ] Hezki reviewed it in Lavish Editor over the Tailscale link, the feedback was applied, and the approval is recorded in this ticket's Notes.
- [ ] `docs/pm/decisions.md` records the Lavish adoption for T001–T003 and `docs/design/` as the mockups' home.

## Subtasks
- [ ] Inventory the four current surfaces (bells + bubbles) in `landing.html` and their actions, and reuse the app's existing CSS tokens
- [ ] Draft the lane (five kinds, both sizes, empty state) from the brainstorm mockup under `.lavish/`
- [ ] Open it with `npx -y lavish-axi`, hand over the Tailscale link, poll for feedback, iterate
- [ ] On approval: export to `docs/design/T001-needs-you-lane.html`, note the approval, add `docs/design/` to nothing else (no static serving)

## Plan
Approach:
Touches:
Tests first:
Decisions to record:
approved: no

## Notes
Migrated from `docs/archive/tickets/051-unified-needs-you-lane/`. Grilled 2026-09-08: the Claude design app is replaced by the lavish-axi skill for T001–T003 (see decisions.md), so this ticket is driven by /pm:work like any other; its "tests" gate is the browser review, not pytest. Review only over the Tailscale/MagicDNS link — `lavish-axi share` uploads to a third-party host and is off limits. Non-goals: new approval types, the home layout (T002), push delivery (already 041), any code under `src/hive/web`.

## Proposed changes
