# Three-altitudes doc structure

## Context

`docs/PROJECT_PLAN.md` had grown to 3,297 lines — sprint history,
architecture overview, future ideas, and cross-cutting concerns all in
one file. Readers (human and AI) couldn't separate current from stale
from aspirational. `STATUS.md` and `ROADMAP.md` were partial attempts
to peel off "now" and "next", but the giant historical record still had
no good home, and reference docs (`README.md`, `CONTEXT.md`,
`DEPLOYMENT.md`) drifted because no rule said when to update them.

## Decision

Project management docs are organised into **three altitudes** (time-tenses):

- `docs/roadmap.md` — vision/themes, months horizon. No tech choices,
  no ticket IDs.
- `docs/sprints/YYYY-QN-SN.md` — 2-week calendar windows, peer files,
  sorted chronologically by filename. Each sprint commits a set of
  Tickets.
- `docs/tickets/NNN-slug/` — one unit of work. A folder of artifacts:
  `ticket.md`, `questions.md`, `research.md`, `design.md`,
  `outline.md`, `plan.md`.

Plus a **reference layer** (orthogonal to altitudes, each with its own
edit rule):

- `CONTEXT.md` — glossary; **free edits**, anytime, no ticket needed.
- `docs/ARCHITECTURE.md` (if added) / `README.md` / `docs/DEPLOYMENT.md`
  — system maps and runbooks; edited **inside the ticket** that
  changed the underlying code (cross-cutting ticket, declared in
  `plan.md`).
- `docs/adr/*.md` — decisions; **append-only**. New decision = new
  file with the next number.
- `docs/CHANGELOG.md` — one line per shipped sprint, added at sprint
  close.

`CLAUDE.md` becomes the agent hub: inlines the working-method rules
(altitudes + the staged ticket workflow) and `@`-references the live
docs (`CONTEXT.md`, current sprint, INDEX).

The 6-artifact ticket workflow (`ticket` → `questions` → `research`
→ `design` → `outline` → `plan`) is **folder-only** for now.
Per-stage sandboxed Claude Code sessions come later when that
infrastructure exists; enforcement without sandboxing is performative.

## Considered options

- **Keep `PROJECT_PLAN.md`, refactor into sections.** Rejected: same
  file, same drift problem. Section discipline degrades the moment
  someone is in a hurry.
- **Full workflow enforcement now (one CC session per artifact).**
  Deferred: multi-session sandboxing isn't built. Enforcing the
  workflow without per-stage context isolation means each Claude Code
  session carries the whole ticket context, defeating the discipline.
- **One time-tense only (just tickets, no sprints).** Rejected:
  sprints provide a calendar-bounded commitment surface and a natural
  freeze point for the CHANGELOG. Without them, tickets drift across
  weeks with no end-of-window ritual.

## Consequences

- Each kind of info has one home; drift surface shrinks.
- Old "Sprint 0–31" terminology is legacy — kept in `CHANGELOG.md` and
  the archived `PROJECT_PLAN.md`. The current meaning of *sprint*
  (2-week calendar window) starts from `2026-Q2-S1`.
- More files. Each ticket gets its own folder; artifacts can be
  produced as needed (no enforcement yet).
- Cross-cutting tickets — ones that touch `ARCHITECTURE.md`,
  `README.md`, or `DEPLOYMENT.md` — declare reference-doc impact in
  `plan.md` so the edit happens in scope, not as a forgotten follow-up.
- Workflow enforcement (per-stage sandboxed sessions) is blocked on
  multi-CC infrastructure. Tracked separately.
- ADR maintenance stays cheap because ADRs are append-only: no
  edit-old-file conflicts, no merge churn.
