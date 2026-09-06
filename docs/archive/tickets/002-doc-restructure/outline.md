# Outline — 8 steps

Each step is reversible; each leaves the repo in a working state.

1. **Lock the design** — write ADR 0003, add `Sprint` and `Ticket`
   terms to `CONTEXT.md`. Pure additions, zero risk.
2. **Scaffold** — `mkdir docs/{sprints,tickets,archive}`; create
   placeholder `INDEX.md` and `CHANGELOG.md`.
3. **Archive legacy** — `git mv docs/PROJECT_PLAN.md` and
   `docs/AUDIT_2026-05-05.md` into `docs/archive/`.
4. **Author current state** — populate `CHANGELOG.md` with past
   sprint entries; write the current sprint file
   (`docs/sprints/2026-Q2-S1.md`); write Tickets 001 and 002 with
   their artifacts; populate `docs/tickets/INDEX.md`.
5. **Rename + trim** — `git mv ROADMAP.md docs/roadmap.md` and trim
   it to themes-only (drop in-flight Phase 1 detail — that lives in
   the sprint file now). Remove `STATUS.md`.
6. **Refresh CLAUDE.md** — inline the CONVENTIONS text; add
   `@`-references to `CONTEXT.md`, `docs/roadmap.md`, the current
   sprint, and `docs/tickets/INDEX.md`. Trim stale `claude -p`
   subprocess language.
7. **Refresh README.md** — drop the stale architecture diagram and
   the outdated `src/hive/` file-tree block. Keep the
   install/quickstart skeleton.
8. **(Deferred)** Clean `docs/DEPLOYMENT.md` — strip `(Sprint NN)`
   stamps and reorganise by what-you-do. Tracked as a future Ticket.
