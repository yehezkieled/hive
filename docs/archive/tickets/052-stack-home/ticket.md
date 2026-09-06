# 052 — Stack home (the Delegator's Desk landing)

> S10 web redesign — the centerpiece. Hosts 051's needs-you lane; opens into 053.

> **Design venue changed.** The visual/UX design for this ticket is now produced
> in the **Claude design app**, not Claude Code — the deliverable here is an
> approved design/mockup, not shipped code. The Claude Code implementation lands
> in **ticket 065** (Implement the Delegator's Desk redesign).

## What

Replace today's fleet-monitor landing with the Delegator's Desk **Stack** home:

- **Needs-you lane** (051) as the **hero** — loud when non-empty; a calm
  "✓ all clear · N loops running" when empty.
- **Project-glance cards** — per project: loop status (running / idle / blocked),
  what it's doing now, progress; **tap → the Work view (053)**. Reads the Project
  registry (024) + maestro state + workflow progress (017).
- **Delegate bar** — always present; give the active project's maestro a **goal**
  in plain language (routes via `/api/command`). Shows the target
  ("▸ delegating to \<project\>").
- **Quota chip** — ambient plan-quota gauge in the chrome (the "about to stall"
  signal), promoted from the type-only `/quota`.

Principle: **"default calm, exceptions loud."** Portrait-first iPad.

## Why

Today's landing is an unusable fleet-monitor. The Stack home is the
delegate-and-supervise surface for autonomous loops — the actual daily driver
(vision: A · the Delegator's Desk).

## Acceptance

- Home renders needs-you hero + project cards + delegate bar + quota chip; calm
  empty state.
- Tapping a project opens/focuses its Work-view tab (053).
- Delegate sends a goal to the selected project's maestro.
- `ruff` + `pytest -m "not integration"` green; **deployed re-smoke on a real
  iPad** (portrait + landscape).

## Non-goals

- The Work view internals (053) · project create/management (backlog 058) · new
  observability widgets.
