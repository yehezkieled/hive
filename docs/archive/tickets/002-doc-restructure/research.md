# Research

## Current doc inventory (2026-05-26)

| File | Lines | State |
|---|---|---|
| `README.md` | 153 | **Stale** — describes `claude -p` subprocess model; file-tree predates `runtime/` |
| `CLAUDE.md` | 51 | Healthy — project-specific agent rules |
| `CONTEXT.md` | 102 | Healthy — domain glossary |
| `STATUS.md` | 67 | Recent (2026-05-20) — one-screen current-state ledger |
| `ROADMAP.md` | 90 | Healthy — forward-looking phases |
| `docs/PROJECT_PLAN.md` | **3,297** | **Bloated** — Sprint 0–31 history + architecture + future + cross-cutting, all mashed |
| `docs/DEPLOYMENT.md` | 1,137 | Large — has sprint-stamped notes (`(Sprint 13)`, `(Sprint 14)`, etc.) embedded throughout |
| `docs/AUDIT_2026-05-05.md` | 270 | Point-in-time spec-drift snapshot |
| `docs/adr/*` | 2 ADRs | Clean |
| `docs/plans/*` | 3 active | Recent implementation plans |

## Identified problems

1. `PROJECT_PLAN.md` is four documents in one: sprint history,
   architecture, roadmap-shaped future ideas, cross-cutting concerns.
2. `README.md` describes the pre-migration architecture (`claude -p`
   subprocess per Turn); the codebase has moved to the PTY runtime.
3. `DEPLOYMENT.md` has sprint-stamps inline (`Sprint 13 note: mcp was
   added`, `Web dashboard (Sprint 14)`, etc.) — history rot in a
   runbook.
4. No `CHANGELOG.md` exists.
5. No `ARCHITECTURE.md` exists (only a stale diagram in README).
6. `STATUS.md` and `ROADMAP.md` are healthy peels of "now" and "next",
   but the giant historical record has no proper home.
7. The post-sprint ritual is implicit (mentioned in `CLAUDE.md`), not
   codified into a written working method.

## Key existing intent

`ROADMAP.md` Phase 2 already says: *"Dismantle `PROJECT_PLAN.md`:
build history → `CHANGELOG.md`; architecture → the architecture doc;
anything forward-looking → this roadmap."*

The intent existed; execution was gated by Phase 1's 2026-06-15
deadline.

## Codebase signals

- `STATUS.md` was created 2026-05-18 (commit `74949fb`) — the user
  has been peeling layers off `PROJECT_PLAN.md` incrementally.
- The PTY runtime (`runtime/` package, ADR 0001) is in local main but
  not yet deployed. README still describes the old model — drift is
  active right now.
- 31 sprints of history in `PROJECT_PLAN.md` headers; many are
  one-day shipping bursts (Sprints 6–8 all on 2026-04-16). The
  variable cadence means "sprint" as a word doesn't map to a fixed
  duration in the legacy record.

## Existing methodology inputs

The user introduced a **three-altitudes + staged ticket workflow**
from outside this project. Ticket artifacts: `ticket.md`,
`questions.md`, `research.md`, `design.md`, `outline.md`, `plan.md`.
Each stage ideally runs in a fresh sandboxed Claude Code session —
that infrastructure does not exist yet, so workflow enforcement is
deferred.
