# 054 — Hive project cleanup (incl. the redesign mockups)

> **Independent · run LAST in S10** — after the redesign build lands, so the
> mockups' permanent fate is settled before this sweeps.

> **Note.** The redesign moved to the **Claude design app** (implemented via
> ticket 065), so the hand-coded `static/brainstorm/` mockups this ticket strips
> are now superseded.

## What

A single housekeeping pass over the Hive project + deciding the permanent home
for the web-redesign mockups.

- **Redesign mockups.** The 5 Delegator's-Desk brainstorm mockups are parked in
  this ticket's `mockups/` (`layouts`, `design-v1`, `work-view{,-v2,-v3}`.html).
  Decide their final home — keep as reference alongside the redesign tickets,
  fold key screens into their `design.md` as ASCII, or discard — and act on it.
- **Temp served copies.** Remove `src/hive/web/static/brainstorm/` (mockups were
  parked there so the iPad could reach them during brainstorming — they must
  **not** ship in the deployed app).
- **Scratch.** Remove / gitignore the `.superpowers/brainstorm/` companion
  scratch in the worktree.
- **Stale branches + worktrees.** Prune merged `ticket-04x/*` local branches and
  any orphaned brainstorm worktrees under `.claude/worktrees/`.
- **Drive-by tidy.** Any doc drift / dead scratch surfaced during the redesign.

## Why

The web-redesign brainstorm left scratch in places it shouldn't live — HTML
mockups inside the *deployed* `static/` dir, companion files under
`.superpowers/`, and a pile of merged local branches. One cleanup pass, run
**last**, sweeps it once the redesign's real artifacts (ADR + ticket `design.md`)
are in place.

## Acceptance

- Nothing redesign-scratch left under `src/hive/web/static/`; mockups at their
  agreed home (or removed).
- Merged `ticket-04x/*` branches pruned; `.superpowers/` gitignored/removed.
- `ruff` + full `pytest -m "not integration"` green; **no behaviour change**.

## Non-goals

- The redesign build itself (the other S10 web tickets).
- The architecture-deepening refactors (roadmap Phase 8 / a future BE sprint).
