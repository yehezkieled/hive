# 051 — Unified "needs-you" lane

> S10 web redesign. Feeds the Stack home's hero (052).

> **Design venue changed.** The visual/UX design for this ticket is now produced
> in the **Claude design app**, not Claude Code — the deliverable here is an
> approved design/mockup, not shipped code. The Claude Code implementation lands
> in **ticket 065** (Implement the Delegator's Desk redesign).

## What

Consolidate the **four scattered "needs-you" interrupts** into ONE actionable
feed: decision requests (029/038), mode-elevation approvals, vault payment
approvals, and interactive gates (003) — plus blocked/errored loops. Today these
live in 2 header bells + 3 separate SSE bubble types with copy-pasted approve/deny
logic. Produce **one `needs_you` feed** (the data rollup) + **one lane component**
that renders each item with its inline action (reply field / approve+deny),
reused by the home hero and the Work view.

## Why

"Which run needs me" is the scarcest resource (039). Four surfaces for one job is
confusing and duplicated. One lane = the supervise-by-exception core of the
Delegator's Desk — and it kills the approve/deny code copy-pasted across bells +
bubbles.

## Acceptance

- A single `needs_you` rollup covers decision + mode + vault + gate + errored,
  each with entity, kind, prompt/summary, and its action.
- One lane component renders them with inline actions; the 2 bells + 3 bubble
  renderers collapse into it.
- Calm "✓ all clear" empty state.
- Backed by the existing approval/decision APIs (no new mechanics); tested.

## Non-goals

- New approval types · the home layout (052) · push delivery (already 041).
