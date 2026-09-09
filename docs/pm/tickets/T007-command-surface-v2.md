---
id: T007
title: Command surface v2
epic: E03
milestone: M1
status: in progress
priority: P2
depends_on: []
owner: work-t007
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
- [x] `/mode` offers only `yotree` / `yolo` (`edit`/`auto`/`plan` removed from the command); plan mode is still reachable via the grill-me skill.
- [x] Spawn defaults come from one source of truth: a maestro spawns `yolo`; a lead spawns `yotree` when its project root is a git repo and `yolo` when it is not; no `lifecycle_manager` force-set remains. A test covers both the git-repo (`yotree`) and no-repo (`yolo`) lead cases.
- [x] Hive `/loop` is removed from the dispatcher, `/help`, and autocomplete; the `loop_mode`/`LOOP_PROMPTS` injection is retired; Hive injects native `/goal <completion condition>` at spawn; the `/help` drift test passes.
- [x] `/model fable` is accepted; the API-billed model set lives in one place; selecting a member prints a clear one-line billing warning; non-members (including `fable` while it is plan-billed) are silent. The warning path has a test even if the set ships empty.
- [x] `/ship <entity>` commits + pushes + opens a PR; `/ship <entity> merge` additionally squash-merges (gated by `HIVE_ALLOW_AUTO_MERGE`); `/ship <entity> "msg"` sets the commit message; `/commit` `/pr` `/merge` are removed or aliased.
- [ ] Check command green; `tests/test_help.py` passes; deployed smoke of each changed command.

## Subtasks
- [x] Mode defaults from one source of truth (lead git-repo → `yotree`, else `yolo`); drop `edit`/`auto`/`plan` from `/mode`
- [x] `/loop` removal + retire `LOOP_PROMPTS`; inject `/goal` at spawn
- [x] `/model fable` + one-place API-billed set + billing warning (verify the set; test the path even if empty)
- [x] `/ship` folding `/commit` `/pr` `/merge`
- [x] Update `tests/test_help.py` + help_text for the changed surface

## Plan
Approach: Four independent command changes in one PR, each built test-first.
1. **Mode, one source of truth.** Add `default_permission_mode(role, cwd)` (maestro→`yolo`; lead→`yotree` if `cwd` is a git repo else `yolo`, via `git rev-parse --is-inside-work-tree`). Call it when a maestro is registered (replacing the line-300 `maestro.permission_mode = "yolo"` force-set) and when a lead is created (replacing `permission_mode=entity.permission_mode`). Restrict `/mode`'s `_execute_mode` to `{yolo, yotree}`; drop `edit`/`auto`/`plan` from the offered set (plan stays via grill-me).
2. **`/loop` → `/goal`.** Remove the `loop` route, `_h_loop`, `_execute_loop`, the `/loop` help entry; delete the `LOOP_PROMPTS` injection from `Entity.build_cli_args` and retire `LOOP_PROMPTS` + `set_loop_mode`. Seed the goal at spawn via `seed_goal(prompt)` (verified seedable via `claude -p "/goal …"`, docs/goal.md) covered by a unit test. `send_to_entity` grows an explicit `seed_goal` flag: it wraps the prompt as `/goal <condition>` only on the first turn of a genuine **task** delivery. That flag is set by the user/command entrypoint (`dispatch._send_to_entity`; the local CLI) — never by internal sends (scheduler pokes, peer mail, compact reseeds), which share the same chokepoint.
3. **`/model fable` + billing warning.** Add `fable` to `_execute_model`'s valid set. Put `API_BILLED_MODELS: frozenset[str]` in one place — **empty today** (all models, `fable` included, run plan-billed on the Max plan); when the chosen model is in the set, append a one-line billing warning. Unit-test the warning helper with a member injected so the path is covered though the set ships empty.
4. **`/ship`.** Add a `ship` route → `GitCommands.ship`: `/ship <e>` commit(if msg)+push+PR; `/ship <e> merge` then squash-merge (gated by `HIVE_ALLOW_AUTO_MERGE`); `/ship <e> "msg"` custom message. Remove `commit`/`pr`/`merge` routes + help entries. Update `help_text` (add `ship`) and `tests/test_help.py`.
Touches: `src/hive/commands/dispatch.py`, `src/hive/commands/git_commands.py`, `src/hive/models/entity.py`, `src/hive/process/lifecycle_manager.py`, `src/hive/process/loops.py`, `src/hive/process/message_dispatcher.py`, `src/hive/process/manager.py`, `src/hive/process/git_ops.py`, `src/hive/runtime/claude_adapter.py`, `src/hive/cli/local.py`, `src/hive/telegram/help_text.py`, `src/hive/telegram/commands.py`, `docs/DEPLOYMENT.md`, `tests/` (test_help + new tests).
Tests first: `test_mode_offers_only_yolo_yotree`; `test_spawn_default_mode_one_source` (lead+git→yotree, lead no-git→yolo, maestro→yolo); `test_loop_removed_goal_seeded_at_spawn` (+ help drift); `test_model_fable_accepted_and_api_billed_warning`; `test_ship_folds_commit_pr_merge`.
Decisions to record: recorded in `docs/pm/decisions.md` (2026-09-09 entry).
approved: yes

## Notes
Migrated from `docs/archive/tickets/064-command-surface-v2/`. Non-goals: the pure removals done in 050, the per-harness `/model` extension (T015/T016), building the approval buttons (T004), web UI for any kept command.

Grill (2026-09-09). Original item (4) — removing typed `/approve` `/deny` `/vault` — split out into **T017** (`depends_on: T004`), since it is hard-gated on the buttons while items (1)–(3) and (5) are independent. Design decisions taken (recorded in `decisions.md`):
- **`/goal` seeding:** Hive injects `/goal <completion condition>` at spawn (deterministic, mirrors the JD/loop-prompt path); the `loop_mode`/`LOOP_PROMPTS` machinery is retired, not kept alongside.
- **Billing warning:** a named API-billed model set kept in one place drives the warning. `fable` is added as a valid `/model` name but is **not** assumed API-billed — Fable 5.1 runs under the user's Max plan today. Verify genuine API-billed names in plan mode; if none, ship the set empty and cover the warning path with a unit test.
- **`/mode` default:** lead default is `yotree` only when the project root is a git repo, else `yolo` (yotree requires a git worktree). Both branches are tested.

Review (2026-09-09, verifier + reviewer on Sonnet). Findings fixed in-scope:
- **`/goal` over-seeding (must-fix).** `send_to_entity` is a shared chokepoint; keying seeding on "first turn" (`session_id is None`) wrapped a peer poke or a compact reseed as the entity's `/goal`. Fixed with an explicit `seed_goal` flag set only by the genuine user/command task entrypoint (see Plan step 2 + decisions.md). New tests: `test_internal_first_turn_send_is_not_seeded`, `test_user_command_path_seeds_goal`, and a compact-reseed assertion (`sent_seed_goal == [False, False]`).
- **`/ship` swallowed commit failures (should-fix).** `_execute_ship` treated every `commit` failure alike and pushed + opened a PR regardless. Now a genuine failure (git add error, hook rejection) aborts before push/PR (`Ship aborted: …`), while a benign "nothing to commit" still pushes existing commits + opens the PR. New tests: `test_ship_aborts_on_genuine_commit_failure`, `test_ship_proceeds_when_nothing_to_commit`.
- **Stale `docs/DEPLOYMENT.md`.** Refreshed the `/mode`, `/loop`, `/model`, and `/commit`/`/pr`/`/merge` command docs to the new `yolo|yotree` surface, `/goal` seeding, `/model fable`, and `/ship`.
- **Under-tested `create_team` wiring.** Added integration tests driving the lead-mode default through `create_team` itself: `test_create_team_lead_mode_yotree_in_git_repo`, `..._yolo_without_git_repo`.
- **Misleading comment** in `dispatch._execute_model` corrected.

## Proposed changes
- **Remove dead `GitCommands._execute_commit`** (out-of-scope nit from review). `/ship` reimplements commit+audit inline because it needs the `(ok, summary)` result to decide whether to push; `_execute_commit` returns a formatted string and now has no production caller (only its own unit tests). Fold the shared logic or drop the handler in a follow-up. `_execute_pr`/`_execute_merge` stay — `/ship` calls them.
