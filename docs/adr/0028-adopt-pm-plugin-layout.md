# Adopt the pm plugin layout for planning

## Status

Accepted, 2026-09-06. Supersedes [ADR 0003](0003-three-altitudes-doc-structure.md).

## Context

ADR 0003 organised planning into three altitudes: `docs/roadmap.md`
(months), `docs/sprints/` (2-week calendar windows), and
`docs/tickets/NNN-slug/` (a folder of six artifacts per ticket:
ticket → questions → research → design → outline → plan). It served
ten sprints and 67 tickets, but two parts never earned their weight:

- **Sprints were calendar windows.** They needed opening, extending
  (S10 was pushed out twice), closing, a CHANGELOG line, and a
  `CLAUDE.md` pointer bump each time. None of that changed what got
  built; milestones as *ordered versions* carry the same intent with
  no dates to maintain.
- **The six-artifact chain was never automated.** The per-stage
  sandboxed session it was designed for was not built, so most
  tickets held only `ticket.md` and sometimes `plan.md`. The
  structure implied a rigour the practice did not have.

The `pm` plugin (`/home/hezki/projects/pm-plugin`) provides a smaller
layout with tooling behind it: `milestone → epic → ticket → subtask`,
a `pm.py` that draws dependency flows, validates, claims, and mirrors
to GitHub issues, and skills that grill a ticket (`/pm:grill`), build
it end to end with TDD, checks, and two read-only reviews
(`/pm:work`), and show the board (`/pm:status`).

## Decision

Planning moves to the pm plugin layout:

```
docs/pm/roadmap.md            milestones in order (M1, M2, …) + Backlog
docs/pm/epics/Exx-slug.md     goal, ticket list, dependency Flow
docs/pm/tickets/Txxx-slug.md  one file: What / Why / Acceptance / Subtasks / Plan
docs/pm/decisions.md          process + tooling decisions
CONTEXT.md  ## pm             machine-read config: flow, gates, check command
```

- **Milestones are ordered versions, not dates.** M1 = the
  Delegator's Desk (former Phase 5), M2 = Dogfood on Hive (Phase 6),
  M3 = Harness adapters (Phase 7). Phase 8 ideas sit in Backlog.
- **The old planning docs are archived, not deleted:**
  `docs/archive/roadmap-phases.md`, `docs/archive/sprints/`,
  `docs/archive/tickets/`. Open tickets 047, 051–065 were migrated to
  `T001`–`T016`; done tickets are history only.
- **ADRs stay where they are.** `docs/adr/` remains the append-only
  home for architecture decisions. `docs/pm/decisions.md` holds the
  smaller process decisions the pm workflow makes (flow, mirror,
  gate exceptions).
- **Gates:** `tdd, checks, review, docs`. Check command:
  `uv run ruff check src/ tests/ && uv run ruff format --check src/ tests/ && uv run pytest -m "not integration"`.
- **Flow:** `branch-pr`, merge confirmed by hand; GitHub issues
  mirrored one way from the markdown.

## Consequences

- One place for live planning; `CLAUDE.md` auto-loads
  `docs/pm/roadmap.md` instead of a sprint file and the ticket INDEX.
- No sprint ceremonies. Closing a milestone is `/pm:retro`.
- Grilling replaces the questions → research → design → outline
  chain. A ticket that is a design choice carries `plan: required`
  and gets its plan approved before code.
- Reference-doc rules from ADR 0003 (glossary free edits, README /
  DEPLOYMENT edited inside the ticket that changed the code, ADRs
  append-only, CHANGELOG one line per shipped milestone) still hold.
- Legacy ticket numbers (`001`–`067`) stay valid in ADRs, commit
  messages, and the glossary as history; new work uses `Txxx`.
