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
ready: yes
issue: 269
pr:
---
## What
Design, as an HTML mockup reviewed in Lavish Editor (decisions.md 2026-09-08), the Delegator's Desk **Stack** home that replaces today's fleet-monitor landing: the needs-you lane (T001) as the hero (loud when non-empty, a calm "✓ all clear · N loops running" when empty); project-glance cards per project (loop status running / idle / blocked, what it is doing now, progress; tap opens the Work view, T003); an always-present delegate bar that gives the active project's maestro a goal in plain language and shows the target; and an ambient quota chip in the chrome. Principle: default calm, exceptions loud. Portrait-first iPad. Implementation is T004.

## Why
Today's landing is an unusable fleet-monitor. The Stack home is the delegate-and-supervise surface for autonomous loops, the actual daily driver (ADR 0027).

## Acceptance
- [ ] `docs/design/T002-stack-home.html` opens and shows the Stack home: the needs-you hero (reusing the T001 lane component and tokens), project-glance cards (each with running/idle/blocked status, current activity, and progress), an always-present delegate bar with a visible target, and an ambient quota chip.
- [ ] The file shows both the calm empty state (quiet hero, "N loops running") and the loud non-empty state (hero populated).
- [ ] The file shows a portrait frame (the real layout) and a landscape frame proving the same components reflow.
- [ ] Tapping a project card visibly selects it (and retargets the delegate bar to that project's maestro), captioned as opening/focusing its Work-view tab.
- [ ] Hezki reviewed the mockup and approved it (recorded in this ticket's Notes); the approved file is exported to `docs/design/`.

## Subtasks
- [ ] Draft the portrait home in `.lavish/T002-stack-home.html` around the T001 lane component, on the live `landing.css` tokens
- [ ] Design the project-card states (running / idle / blocked), card-selects-target behaviour, delegate bar, quota chip, and the landscape reflow frame
- [ ] Open with `npx -y lavish-axi`, review over the Tailscale link, iterate, export the approved file to `docs/design/`

## Plan
Approach:
Touches:
Tests first:
Decisions to record:
approved: no

## Notes
Migrated from `docs/archive/tickets/052-stack-home/`. Lavish design ticket, driven by /pm:work; the tdd gate is the browser review. Non-goals: the Work view internals (T003), project create/management (T011), new observability widgets.

**Grilled 2026-09-14. Design decisions settled (for T004 to implement):**
- **Delegate target = card selects it.** Tapping a project card selects it (highlighted) and the delegate bar reads "Delegate to <maestro>" and sends there. A default target is chosen on load (the PA maestro, or the sole project). One target, no separate picker.
- **Quota chip = worst of the two windows.** One compact chip shows the higher-utilization of the 5-hour and 7-day account-wide windows as a percent with a calm→warn→hot colour; tap reveals both windows. Ambient, matches "default calm".
- **Landscape = portrait-first with a reflow frame.** Portrait is the real layout; one landscape frame shows the same components reflowing (cards multi-column, lane stays hero). Not two separately-designed layouts.
- Reuse the exact T001 lane component (`.nyl` / `.nyi` classes) for the hero — do not re-draw it. Carry over T001's 44px-touch-target requirement.

## Proposed changes
