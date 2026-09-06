# 016 — Outline

Implementation structure for the design (D1–D7). Three vertical slices,
each independently shippable, plus the cross-cutting doc sweep that
rides with whichever slice touches the surface it documents.

## Slice 1 — Deny Worker creation, with feedback (D1, D2)

The enforcement core. One PR.

1. `src/hive/bus/permissions.py` — `can_spawn_worker` returns `False`
   for every actor (drop the maestro and lead arms; keep the function —
   018 deletes it). Docstring states ADR 0013.
2. `src/hive/process/message_dispatcher.py` — the `spawn_worker` branch
   (`:471-535`): on permission denial, call `_reject_action` with a
   note naming the replacement ("`spawn_worker` is retired — fan out
   with the Workflow tool"; phrased to not invite a retry). Keep the
   audit (`entity.spawn_worker_denied`) — it is 018's drainage proof.
   The missing-lead and rate-limit guards become unreachable-but-intact
   (018 deletes the branch).
3. Tests:
   - Flip `tests/test_process_manager.py:957-1041` (lead + maestro
     autonomous spawn) to assert deny + audit.
   - Flip `tests/process/test_message_dispatcher.py:422-438` (kickoff
     tracking) accordingly.
   - New: denied actor *receives* the rejection note (the feedback
     asymmetry was the design's biggest hazard — test it directly).
   - Leave mechanism tests (lifecycle, actions parsing, roundtrip)
     untouched — they cover code 018 deletes.

## Slice 2 — Trim every prompt surface; add the Workflow rules (D3, D4, D5)

The behavioral migration. One PR. No Hive code — personalities + two
prompt strings + JD tests.

1. `personalities/role-lead.md` — delete the legacy block (`:98-143`:
   spawn_worker docs, kill_entity docs, spawn template, JSON-escaping
   note). Extend the Workflow section (015's) with:
   - failure enumeration rule (retry once sharpened → name failures in
     the synthesis; never silently drop),
   - bounded fan-out + distilled results rule (~10–20 agents/run,
     schema-shaped returns, sequential runs for bigger jobs),
   - tag-hygiene rule (leaf prompts forbid literal `<hive_actions>`/any
     tag; synthesis paraphrases, never quotes),
   - worktree policy (release-granularity test: default shared
     worktree → one PR; independently-shippable slices →
     `isolation:'worktree'` + per-slice PRs; escape-hatch isolation =
     merge back + remove in the same turn).
2. `personalities/role-maestro.md:100-101` — remove the `spawn_worker`
   verb docs.
3. `src/hive/process/scheduler.py:8,197` — facts prompt: drop
   `spawn_worker`, keep `spawn_team` / `kill_entity`.
4. `src/hive/process/wake_scheduler.py:25-30` — `_SPAWN_KICKOFF_TEXT`:
   drop the "Spawn workers if the work warrants subdivision" sentence.
5. Tests: rewrite `tests/test_role_jd.py` lead contract — assert the
   new rules present and `spawn_worker` **absent** (flip `:103`); check
   maestro JD likewise; scheduler/kickoff text assertions if any exist.

## Slice 3 — Remove the `/worker spawn` arm (D1)

The user-path closure. One PR.

1. `src/hive/commands/dispatch.py` — remove the `spawn` arm of the
   `/worker` command (`:701` region). **Keep `/worker kill`** — it is
   how pre-existing stragglers are killed at deploy; 018 deletes the
   whole command.
2. `src/hive/telegram/help_text.py:115-119` — usage/description/examples
   reflect kill-only.
3. `docs/DEPLOYMENT.md:766` — command reference line updated.
4. Tests: `/worker spawn` returns the unknown/removed-subcommand
   response; `/worker kill` still works.

## Cross-cutting doc sweep (rides with slices)

- `docs/DEPLOYMENT.md:552,557` — autonomous-actions description: drop
  `spawn_worker` from the verb list (ride with Slice 1 or 2).
- `README.md` — no `spawn_worker` mentions found (verified 2026-06-12);
  re-grep at build time.
- INDEX row + sprint tick at close.

## Slice independence

No logical blockers — each slice lands alone and the system stays
coherent at every intermediate state:

- Slice 2 without Slice 1: prompts stop teaching the verb (traffic
  dies); mechanism still answers stragglers.
- Slice 1 without Slice 2: a prompted lead gets the rejection note and
  self-corrects (the feedback path exists precisely for this).
- Slice 3 independent of both (user path only).

File overlap is nil across slices except `docs/DEPLOYMENT.md`
(different lines). Suggested order all-parallel; merge as green.

## Verification (ticket-level, after all slices)

- `ruff check src/ tests/ && ruff format --check src/ tests/`
- Full `pytest -m "not integration"` (the roundtrip test runs here and
  must stay green — it tests 018's machinery, not 016's behavior).
- `grep -rn "spawn_worker" personalities/ src/hive/process/scheduler.py
  src/hive/process/wake_scheduler.py` → zero prompt-surface hits.
- Live (sprint DoD): deployed maestro→lead turn where the lead fans out
  a Workflow run end-to-end; `entity.spawn_worker_denied` audit appears
  if a legacy emission occurs; main checkout stays clean.
