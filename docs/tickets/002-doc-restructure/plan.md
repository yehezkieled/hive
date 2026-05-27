# Plan

## Files this Ticket creates / modifies

| Path | Op | Step |
|---|---|---|
| `docs/adr/0003-three-altitudes-doc-structure.md` | create | 1 |
| `CONTEXT.md` | edit (add Project management section) | 1 |
| `docs/sprints/` | mkdir | 2 |
| `docs/tickets/` | mkdir | 2 |
| `docs/archive/` | mkdir | 2 |
| `docs/CHANGELOG.md` | create (placeholder → content) | 2 → 4 |
| `docs/tickets/INDEX.md` | create (placeholder → populate) | 2 → 4 |
| `docs/PROJECT_PLAN.md` | `git mv` → `docs/archive/` | 3 |
| `docs/AUDIT_2026-05-05.md` | `git mv` → `docs/archive/` | 3 |
| `docs/sprints/2026-Q2-S1.md` | create | 4 |
| `docs/tickets/001-deploy-pty-runtime/{ticket,plan}.md` | create | 4 |
| `docs/tickets/002-doc-restructure/{ticket,questions,research,design,outline,plan}.md` | create | 4 |
| `ROADMAP.md` → `docs/roadmap.md` | `git mv` + edit | 5 |
| `STATUS.md` | delete | 5 |
| `CLAUDE.md` | edit (inline CONVENTIONS, add `@` refs) | 6 |
| `README.md` | edit (drop stale architecture) | 7 |

## Verification

- `ls -1 docs/` matches the structure in `design.md`.
- `cat docs/tickets/INDEX.md` lists Tickets 001 and 002 with status.
- `git log --diff-filter=R --summary -1` shows `PROJECT_PLAN.md` and
  `AUDIT_2026-05-05.md` renamed (not deleted + re-added).
- `wc -l` on each refreshed file shows tight, focused sizes.
- All existing tests still pass (no application code touched).
- Manually re-read `CLAUDE.md` and confirm the agent context loads
  cleanly with the `@` references resolved.

## Out of scope for this Ticket

- `docs/ARCHITECTURE.md` creation — deferred (not minimum viable).
- `docs/DEPLOYMENT.md` sprint-stamp cleanup — separate future Ticket.
- Workflow enforcement — deferred until multi-CC sandbox exists.
- Migrating `docs/plans/` content to `docs/tickets/` — separate Ticket
  if/when needed; for now `docs/plans/` is left alone as a holding
  area for not-yet-restructured work.

## Cross-cutting impact

Declared up front (see `design.md` § "Cross-cutting impact"):
`CONTEXT.md`, `README.md`, `CLAUDE.md`, new ADR.
