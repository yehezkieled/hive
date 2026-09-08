---
id: T006
title: Lead JD drives pattern-skill invocation
epic: E03
milestone: M1
status: in progress
priority: P2
depends_on: []
owner: work-t006
auto: no
plan: none
ready: yes
issue: 273
pr:
---
## What
Reword the `## Interaction patterns` intro of `personalities/role-lead.md` (the 044 awareness pointer) so a Lead **invokes the matching global skill with the Skill tool** when a fan-out fits a known shape, instead of borrowing the shape's name and hand-authoring the Workflow. Design settled at grilling as option (a): the JD distinguishes the one **embedded** recipe (`debate`, which the Lead authors itself from the inline skeleton) from the **invoke-the-skill** patterns (`split`, `sweep`, `compete`, `double-check`, named as examples), and keeps the free-form fallback for when no skill fits. The `### debate` recipe, 044's self-select framing, and `role-maestro.md` are untouched. Precondition for the live check: the four pattern skills are copied from `~/projects/claude-code-setup/home/.claude/skills` into `~/.claude/skills` on this host (they are not installed today; a host chore, not repo work).

## Why
The 2026-06-29 behavioural test showed 044's pointer makes a Lead shape-aware and self-selecting but not skill-invoking: it named `split` and hand-authored the Workflow with 0 Skill calls. The value of the global pattern skills is their refined recipes (double-check's majority vote, sweep's loop-until-dry, compete's judge panel); awareness without invocation leaves most of that value on the table and works against the loop self-organizing well.

## Acceptance
- [ ] `personalities/role-lead.md` says `debate` is the one embedded recipe the Lead authors itself, and every other shape is a global skill the Lead INVOKES via the Skill tool (naming split / sweep / compete / double-check as examples) when a fan-out fits, authoring free-form only when no skill fits.
- [ ] `tests/test_role_jd.py` has a new test asserting the invoke wording; the existing 034 (debate) and 044 (awareness) tests pass unchanged.
- [ ] `uv run ruff check src/ tests/`, `uv run ruff format --check src/ tests/`, and `uv run pytest -m "not integration"` are green.
- [ ] After merge + deploy, with the four pattern skills installed in `~/.claude/skills`, a live Lead given a double-check-shaped task (verify N claims) shows a `Skill` tool call to a pattern skill in its transcript, not a hand-authored Workflow. Evidence (transcript path + the call) is recorded under Notes before the ticket is marked done.

## Subtasks
- [x] Failing test in `tests/test_role_jd.py` for the invoke wording (red)
- [x] Reword the `## Interaction patterns` intro in `personalities/role-lead.md` (green); keep `### debate` and the 044 assertions intact
- [x] Run the check command
- [x] Install the four pattern skills into `~/.claude/skills` on this host (precondition for the live check)
- [ ] After merge + deploy: live Lead re-test on a double-check-shaped task; record the evidence under Notes

## Plan
Approach: Rewrite the "More patterns live in your skills" paragraph of `## Interaction patterns` in the lead JD into two labelled halves — `debate` as the one **embedded** recipe the Lead authors from the inline skeleton, and every other shape as a **global skill the Lead invokes with the Skill tool**, naming `split` / `sweep` / `compete` / `double-check` as examples and keeping "no skill fits, author free-form" as the explicit fallback. Test first in `tests/test_role_jd.py`; the 034 and 044 tests stay untouched as regression guards.
Touches: `personalities/role-lead.md`, `tests/test_role_jd.py`.
Tests first: `test_lead_jd_drives_pattern_skill_invocation` — asserts the invoke wording on the flattened JD text: the Skill tool named, the example skills, the embedded-vs-invoked split, the free-form fallback.
Decisions to record: the (a)-over-(b) design call, already written into `docs/pm/decisions.md` (2026-09-08 entry).
approved: yes

## Notes
Migrated from `docs/archive/tickets/047-lead-pattern-skill-invocation/`. Evidence: 2026-06-29 live test, Lead `otter.envscan`, `meta.name: env-var-survey`, 0 Skill calls. Adjacent observation (not in scope): the maestro↔lead confirm handshake stalled ~3 min on a long scheduler interval; worth its own ticket if it recurs. Non-goals: editing the skill files themselves, forcing invocation, any `role-maestro.md` change, converging `debate` into a skill (option (b), rejected at grilling).

Grill (2026-09-08): design (a) chosen over (b); the pattern skills were found absent from `~/.claude/skills` on this host (present only in `~/projects/claude-code-setup`), so installing them is a stated precondition of the live check, not ticket work.

## Proposed changes
