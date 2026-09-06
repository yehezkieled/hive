---
id: T006
title: Lead JD drives pattern-skill invocation
epic: E03
milestone: M1
status: todo
priority: P2
depends_on: []
owner:
auto: no
plan: required
ready: no
issue: 273
pr:
---
## What
Strengthen the lead role file's pattern pointer (added in ticket 044) so that when a fan-out matches a known coordination shape the Lead **invokes the matching global skill** (getting its refined, tested recipe) instead of borrowing the shape's name and hand-authoring the Workflow. Reword from "scan your skills and reach for one" toward "invoke the matching /skill when a fan-out fits a known shape", and resolve the likely root confusion: the inline `debate` recipe is an embedded pattern the Lead authors itself, which sets a "patterns = author them yourself" precedent. Design call at grilling: (a) distinguish the one embedded recipe from the invoke-the-skill patterns in the JD, or (b) converge `debate` to a skill too. Likely (a).

## Why
The 2026-06-29 behavioural test showed 044's pointer makes a Lead shape-aware and self-selecting but not skill-invoking: it named `split` and hand-authored the Workflow with 0 Skill calls. The value of the global pattern skills is their refined recipes (double-check's majority vote, sweep's loop-until-dry, compete's judge panel); awareness without invocation leaves most of that value on the table and works against the loop self-organizing well.

## Acceptance
- [ ] The lead JD wording drives skill invocation when a shape fits, without removing the Lead's freedom to author free-form when no skill matches.
- [ ] A behavioural re-test on a non-trivial coordination task (e.g. double-check or sweep) shows a `Skill` call in the Lead's transcript, not a hand-authored Workflow.
- [ ] 044's awareness framing and the inline `debate` recipe still work.
- [ ] Prompt-assembly test updated or added; check command green.

## Subtasks
- [ ] Reproduce the gap on a harder task (single-observation caveat)
- [ ] Decide (a) vs (b) and reword the pointer
- [ ] Re-test and update the prompt-assembly test

## Plan
Approach:
Touches:
Tests first:
Decisions to record:
approved: no

## Notes
Migrated from `docs/archive/tickets/047-lead-pattern-skill-invocation/`. Evidence: 2026-06-29 live test, Lead `otter.envscan`, `meta.name: env-var-survey`, 0 Skill calls. Adjacent observation (not in scope): the maestro↔lead confirm handshake stalled ~3 min on a long scheduler interval; worth its own ticket if it recurs. Non-goals: editing the skill files themselves, forcing invocation, any `role-maestro.md` change.

## Proposed changes
