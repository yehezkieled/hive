# 002 — Restructure project management docs

## What

Dismantle `docs/PROJECT_PLAN.md` (3,297 lines) and reorganise project
management documentation into **three altitudes** (roadmap / sprints /
tickets) plus a **reference layer** (CONTEXT, README, DEPLOYMENT,
CHANGELOG, ADRs). Adopt the staged ticket-folder workflow with full
enforcement deferred.

## Why

`PROJECT_PLAN.md` had become a 3,297-line "everything bucket" — sprint
history, architecture overview, future ideas, and cross-cutting
concerns all in one file. Readers (human or agent) couldn't separate
current from stale from aspirational. Reference docs drifted (README
described the pre-PTY architecture; DEPLOYMENT had sprint-stamp cruft
embedded) because no rule said when to update them.

`ROADMAP.md` Phase 2 already named this work — the intent existed;
execution was gated by Phase 1's deadline. We're doing it now because
the new structure makes Phase 2's coming `manager.py` breakup cleaner
to track.

## Acceptance

- `docs/sprints/`, `docs/tickets/`, `docs/archive/` exist and are
  populated.
- `docs/PROJECT_PLAN.md` and `docs/AUDIT_2026-05-05.md` archived.
- `docs/CHANGELOG.md` lists past Sprints 0–31, one line each.
- `docs/sprints/2026-Q2-S1.md` defines the current sprint.
- `docs/tickets/INDEX.md` registers Tickets 001 and 002.
- `ROADMAP.md` renamed to `docs/roadmap.md`, trimmed to themes-only.
- `STATUS.md` removed; its content folded into the sprint file.
- `CLAUDE.md` inlines the CONVENTIONS text and `@`-references the
  live docs.
- `README.md` no longer claims the stale `claude -p` subprocess model.
- ADR 0003 captures the decision.
- `CONTEXT.md` defines the new `Sprint` and `Ticket` terms.
