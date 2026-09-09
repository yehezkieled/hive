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
ready: yes
issue: 274
pr:
---
## What
Four behaviour changes to commands the 050 audit kept (item 4 of the original five — removing typed `/approve` `/deny` `/vault` — is split into **T017**, hard-gated on T004's buttons):
(1) `/mode` — drop `edit`, `auto`, and `plan` from the offered set, leaving `yotree` / `yolo`; plan mode stays reachable via the grill-me skill, not a per-entity toggle. Enforce the spawn default from **one source of truth**: maestros = `yolo`; leads = `yotree` **only when the project root is a git repo**, else the lead falls back to `yolo` (yotree needs a git worktree). No downstream force-set drift (today the `Entity` base default is `default` and `lifecycle_manager.py` force-sets maestros to `yolo`).
(2) Remove Hive's `/loop` in favour of Claude Code's native `/goal`. Decision (grill): **Hive injects `/goal <completion condition>` at spawn** — the deterministic path the role JD + loop prompt use today — and the `loop_mode` / `LOOP_PROMPTS` injection is retired in its favour.
(3) `/model` — add `fable` to the valid set and drive a one-line billing warning from a **named API-billed model set kept in one place**; selecting a member of that set warns, others are silent. `fable` is a valid model name but **not assumed API-billed** — the user runs Fable 5.1 under the Max plan today. Verify which model names are genuinely API-billed before filling the set; if none qualify, the set ships **empty** and the warning path is covered by a test only.
(4) Fold `/commit` `/pr` `/merge` into one `/ship <entity> [merge|"msg"]`: `/ship <entity>` commits + pushes + opens a PR; `/ship <entity> merge` additionally squash-merges (still gated by `HIVE_ALLOW_AUTO_MERGE`); `/ship <entity> "msg"` sets a custom commit message.

## Why
050 gave the redesign a smaller command set; this makes the kept commands behave right before they are promoted to first-class UI. Each change removes a foot-gun (mode inconsistency, silent API billing), retires a soon-confusing command (`/loop` vs the loop-engineering direction), or turns a three-command dance into one verb.

## Acceptance
- [ ] `/mode` offers only `yotree` / `yolo` (`edit`/`auto`/`plan` removed from the command); plan mode is still reachable via the grill-me skill.
- [ ] Spawn defaults come from one source of truth: a maestro spawns `yolo`; a lead spawns `yotree` when its project root is a git repo and `yolo` when it is not; no `lifecycle_manager` force-set remains. A test covers both the git-repo (`yotree`) and no-repo (`yolo`) lead cases.
- [ ] Hive `/loop` is removed from the dispatcher, `/help`, and autocomplete; the `loop_mode`/`LOOP_PROMPTS` injection is retired; Hive injects native `/goal <completion condition>` at spawn; the `/help` drift test passes.
- [ ] `/model fable` is accepted; the API-billed model set lives in one place; selecting a member prints a clear one-line billing warning; non-members (including `fable` while it is plan-billed) are silent. The warning path has a test even if the set ships empty.
- [ ] `/ship <entity>` commits + pushes + opens a PR; `/ship <entity> merge` additionally squash-merges (gated by `HIVE_ALLOW_AUTO_MERGE`); `/ship <entity> "msg"` sets the commit message; `/commit` `/pr` `/merge` are removed or aliased.
- [ ] Check command green; `tests/test_help.py` passes; deployed smoke of each changed command.

## Subtasks
- [ ] Mode defaults from one source of truth (lead git-repo → `yotree`, else `yolo`); drop `edit`/`auto`/`plan` from `/mode`
- [ ] `/loop` removal + retire `LOOP_PROMPTS`; inject `/goal` at spawn
- [ ] `/model fable` + one-place API-billed set + billing warning (verify the set; test the path even if empty)
- [ ] `/ship` folding `/commit` `/pr` `/merge`
- [ ] Update `tests/test_help.py` + help_text for the changed surface

## Plan
Approach:
Touches:
Tests first:
Decisions to record:
approved: no

## Notes
Migrated from `docs/archive/tickets/064-command-surface-v2/`. Non-goals: the pure removals done in 050, the per-harness `/model` extension (T015/T016), building the approval buttons (T004), web UI for any kept command.

Grill (2026-09-09). Original item (4) — removing typed `/approve` `/deny` `/vault` — split out into **T017** (`depends_on: T004`), since it is hard-gated on the buttons while items (1)–(3) and (5) are independent. Design decisions taken (recorded in `decisions.md`):
- **`/goal` seeding:** Hive injects `/goal <completion condition>` at spawn (deterministic, mirrors the JD/loop-prompt path); the `loop_mode`/`LOOP_PROMPTS` machinery is retired, not kept alongside.
- **Billing warning:** a named API-billed model set kept in one place drives the warning. `fable` is added as a valid `/model` name but is **not** assumed API-billed — Fable 5.1 runs under the user's Max plan today. Verify genuine API-billed names in plan mode; if none, ship the set empty and cover the warning path with a unit test.
- **`/mode` default:** lead default is `yotree` only when the project root is a git repo, else `yolo` (yotree requires a git worktree). Both branches are tested.

## Proposed changes
