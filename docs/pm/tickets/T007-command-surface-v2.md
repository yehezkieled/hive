---
id: T007
title: Command surface v2
epic: E03
milestone: M1
status: todo
priority: P2
depends_on: []
owner:
auto: no
plan: required
ready: no
issue: 274
pr:
---
## What
Five behaviour changes to commands the 050 audit kept: (1) `/mode` — drop `edit` and `auto`; enforce the default policy leads = `yotree`, maestros = `yolo` from one source of truth (today the `Entity` base default is `default`, `lifecycle_manager.py` force-sets maestros to `yolo`, and the role files say "prefer yotree"); plan mode stays reachable via the grill-me skill, not a per-entity toggle. (2) Remove Hive's `/loop` in favour of Claude Code's native `/goal`; decide at grilling whether the entity self-invokes `/goal` or Hive injects it at spawn. (3) `/model` — add `fable` and emit a one-line billing warning when an API-billed model is selected. (4) Remove `/approve` `/deny` `/vault` as typed commands once the needs-you lane's buttons exist (T004); backend untouched. (5) Fold `/commit` `/pr` `/merge` into one `/ship <entity> [merge|"msg"]`, auto-merge still gated by `HIVE_ALLOW_AUTO_MERGE`.

## Why
050 gave the redesign a smaller command set; this makes the kept commands behave right before they are promoted to first-class UI. Each change removes a foot-gun (mode inconsistency, silent API billing), retires a soon-confusing command (`/loop` vs the loop-engineering direction), or turns a three-command dance into one verb.

## Acceptance
- [ ] `/mode` offers only `yotree` / `yolo`; spawn defaults enforce leads = `yotree`, maestros = `yolo` from one place with no downstream force-set.
- [ ] Hive `/loop` is removed from dispatcher, help, and autocomplete; the `/goal` seeding path is decided and wired; the `/help` drift test passes.
- [ ] `/model fable` is accepted; selecting an API-billed model prints a clear billing warning; plan-billed selections unchanged.
- [ ] `/approve` `/deny` `/vault` are removed as typed commands only after T004's button surface exists; a payment is approvable end to end at all times.
- [ ] `/ship <entity>`, `/ship <entity> merge`, and `/ship <entity> "msg"` work; the three old commands are removed or aliased.
- [ ] Check command green; `tests/test_help.py` passes; deployed smoke of each changed command.

## Subtasks
- [ ] Mode defaults from one source of truth
- [ ] `/loop` → `/goal` decision + removal
- [ ] `/model fable` + billing warning
- [ ] `/ship`
- [ ] Typed approve/deny/vault removal after T004

## Plan
Approach:
Touches:
Tests first:
Decisions to record:
approved: no

## Notes
Migrated from `docs/archive/tickets/064-command-surface-v2/`. Change (4) is hard-gated on T004; changes (1)–(3) and (5) are independent, so grilling may split this into two tickets. Non-goals: the pure removals done in 050, the per-harness `/model` extension (T015/T016), building the approval buttons (T004), web UI for any kept command.

## Proposed changes
