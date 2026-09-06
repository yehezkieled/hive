---
id: T004
title: Implement the Delegator's Desk redesign
epic: E02
milestone: M1
status: todo
priority: P1
depends_on: [T001, T002, T003]
owner:
auto: no
plan: required
ready: no
issue: 271
pr:
---
## What
Implement the approved Desk designs (T001 lane, T002 Stack home, T003 Work view) into `src/hive/web` and wire the backend no design tool can produce: (1) one `needs_you` rollup folding decision (029/038) + mode-elevation + vault payment + interactive gate (003) + blocked/errored loops, replacing the 2 header bells + 3 SSE bubble renderers with one lane component and its inline actions; (2) the project ↔ home/loop-status binding behind the project-glance cards (project → owning maestro → live run status + quota chip) with the delegate bar reaching the tapped project's maestro via `/api/command`; (3) tab state with active-tab = default-target routing, the Clear popover (view vs +reset), History, and push deep-link opening the right tab (absorbs 049). Plus the on-device iPad adjustments the mockups cannot verify.

## Why
The design app produces the look and interaction of the Desk but not the wiring: SSE feeds, approval/decision APIs, project ↔ loop status, tab routing, and push deep-links live in `src/hive/web` and the backend. One implementation ticket gives the redesign a single clean landing point instead of three half-implemented ones.

## Acceptance
- [ ] The Stack home (needs-you hero + project cards + delegate bar + quota chip) renders and opens into the tabbed Work view, matching the approved designs.
- [ ] A single `needs_you` rollup (decision + mode + vault + gate + errored) backs one lane component with inline actions; the 2 bells + 3 bubble renderers are gone; calm empty state; backed by the existing approval/decision APIs with no new mechanics.
- [ ] Per-project cards render live loop status from the project registry + maestro state + workflow progress; delegate sends a goal to that project's maestro.
- [ ] Active tab = default target; Clear popover offers view-only vs +reset; History recalls past messages; a Web Push deep-link opens the correct tab reply-ready.
- [ ] Each interrupt type (decision, mode, vault, gate) is re-smoked end to end; the full web + approval test suite stays green.
- [ ] Deployed re-smoke on an actual installed iPad PWA (portrait + landscape) for the home and Work view.
- [ ] `uv run ruff check src/ tests/ && uv run ruff format --check src/ tests/ && uv run pytest -m "not integration"` green.

## Subtasks
- [ ] Build the `needs_you` rollup + lane component and delete the bells/bubbles
- [ ] Build the project ↔ loop-status binding and the Stack home
- [ ] Build tab state, default-target routing, Clear/History, deep-link
- [ ] Deploy and re-smoke on the iPad

## Plan
Approach:
Touches:
Tests first:
Decisions to record:
approved: no

## Notes
Migrated from `docs/archive/tickets/065-implement-desk-redesign/`. Largest ticket in M1; consider splitting at grilling if the plan runs long. Non-goals: the visual design itself (T001–T003), new approval types, push delivery mechanics (041), project create/management UI (T011), mid-run Workflow steering (ADR 0014).

## Proposed changes
