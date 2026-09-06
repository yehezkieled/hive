# 065 — Implement the Delegator's Desk redesign (from Claude-design mockups)

> S10 web redesign · **implementation vehicle.** The Claude Code build of the
> designs produced in the Claude design app (051/052/053). Committed to the
> extended [`2026-Q2-S10`](../../sprints/2026-Q2-S10.md).
> **Depends on** 051/052/053 designs being approved.

## What

Implement the approved **Delegator's Desk** redesign into `src/hive/web`. The
visual/UX design is authored in the external **Claude design app** across three
design tickets — 051 (unified needs-you lane), 052 (Stack home), 053 (tabbed Work
view) — whose deliverable is an **approved mockup, not shipped code**. This ticket
is the single Claude Code vehicle that turns those mockups into working web code
and wires the **backend that no design tool can produce**:

- **051 — needs_you rollup:** one `needs_you` feed folding decision (029/038) +
  mode-elevation + vault payment + interactive gate (003) + blocked/errored
  loops, replacing the 2 header bells + 3 SSE bubble renderers with one lane
  component and its inline actions.
- **052 — project ↔ home/loop binding:** the data binding behind the project-
  glance cards (project → owning maestro → live loop/run status + quota chip)
  that drives the Stack home; delegate bar reaches the tapped project's maestro.
- **053 — tab state + routing:** tabbed maestro conversations with
  **active-tab = default target** routing, Clear popover (view vs +reset),
  History, and **push deep-link opens the right tab** (absorbs 049).

Plus **on-device iPad adjustments** — the installed-PWA re-smoke and touch/layout
tweaks the mockups can't verify off-device.

## Why

The Claude design app produces the look and interaction of the Desk, but it can't
produce the wiring — SSE feeds, approval/decision APIs, project↔loop status,
tab-state routing, and push deep-links all live in `src/hive/web` and the
backend. Splitting design (051/052/053, in the design app) from implementation
(this ticket, in Claude Code) keeps each venue doing what it's good at and gives
the redesign one clean landing point in the codebase instead of three
half-implemented ones.

## Acceptance

- The approved 051/052/053 designs are implemented in `src/hive/web`: the Stack
  home (needs-you hero + project-glance cards + delegate bar + quota chip) opens
  into the tabbed Work view.
- **051 backend:** a single `needs_you` rollup (decision + mode + vault + gate +
  errored) backs one lane component with inline actions; the 2 bells + 3 bubble
  renderers collapse into it; calm empty state; backed by existing
  approval/decision APIs (no new mechanics).
- **052 backend:** project ↔ home/loop status binding renders live per-project
  cards; delegate reaches the project's maestro.
- **053 backend:** tab state with active-tab = default-target routing; Clear
  popover (view vs +reset) + History; a Web-Push deep-link opens the correct
  tab.
- **iPad re-smoke** on an actual installed PWA (not just green units) for the
  home and Work view.
- `ruff` + full `pytest -m "not integration"` green; INDEX row for 065 reflects
  final state.

## Non-goals

- The visual/UX **design** itself (owned by 051/052/053 in the Claude design
  app) · new approval types · push delivery mechanics (already 041) · project
  create/management UI (future — backlog 058; S10 only *displays* projects) ·
  mid-run Workflow steering (ADR 0014).
