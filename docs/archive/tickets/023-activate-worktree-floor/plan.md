# Plan — Ticket 023: Activate the worktree floor (isolate leaf work)

FAN-OUT lane. Approach in [`design.md`](design.md); seam map in
[`outline.md`](outline.md); pinning decision in
[ADR 0011](../../adr/0011-session-pinning-over-directory-heuristics.md);
incident evidence in [`research.md`](research.md).

**Dependency:** none blocking (gates 016 — this lands first).

## Slices

| Summary (what the slice delivers) | Issue | Type | Blocked by |
|-----------------------------------|-------|------|------------|
| Worktree floor live: production composition builds a real `WorktreeManager` via a testable factory | [#92](https://github.com/yehezkieled/hive/issues/92) | AFK | — |
| Session pinning: adapters read their exact transcript by session id, heuristic = fallback | [#93](https://github.com/yehezkieled/hive/issues/93) | AFK | — |
| Messaging self-heal: `maestro`/`parent` alias + rejection feedback + spawn-failure notes | [#94](https://github.com/yehezkieled/hive/issues/94) | AFK | — |
| Turn-end inbox check: busy entities never strand queued mail | [#95](https://github.com/yehezkieled/hive/issues/95) | AFK | — |

## Execution waves

- Wave 1: #92, #93, #94, #95 (parallel — #94/#95 overlap on
  `message_dispatcher.py`, which is merge-order territory, not a
  logical blocker)

## Conventions

- Branch `ticket-023/issue-<n>-<slug>`, target `main`, squash-merge
- Validation gate (every PR): `ruff check src/ tests/ && ruff format
  --check src/ tests/` + `pytest -m "not integration"` (75% coverage
  floor)
- Autonomy: all AFK — auto-merge on green CI (owner preapproved this
  run, 2026-06-11)

To build: run the fleet Workflow against this `plan.md`.

## Verification

### Hermetic (CI — proves the seams)
- Production composition factory passes a real `WorktreeManager` (no
  fakes) pointed at the config paths.
- F3 reproduction: decoy `.jsonl` growing in the shared project dir;
  pinned reader still reads its own transcript; missing pid-state file
  → fallback + loud warning; respawn re-pins.
- Alias resolution per role; rejected actions audit + feed back to the
  sender; kickoff failure notes the maestro.
- Mail queued mid-turn wakes the entity exactly once at turn end;
  budget-exhausted and empty-queue paths are quiet.
- `ruff check` + `ruff format --check` (separate gates); full
  `pytest -m "not integration"` green.

### Live smoke (deployed — NOT in CI; owner-run, morning of 2026-06-12)
- Deploy from the MAIN repo (`git -C ~/projects/hive pull` →
  `systemctl --user restart hive.service` → journal check).
- A maestro→lead→leaf turn: lead worktree visible in
  `git worktree list`; `/proc/<lead-pid>/cwd` ≠ repo root; the lead's
  proposal arrives addressed as `maestro`; main checkout stays clean.
  This is 015's deferred live DoD and the sprint DoD's "end-to-end on
  deployed code".

## Out of scope (handoffs)

- **016:** `spawn_worker` → Workflow migration; changed leaf-worktree
  cleanup (commit→PR→merge→remove).
- **020:** auto-bounce/healing of jammed or dead sessions (023
  notifies; 020 heals).
- **021:** the `user` router queue (distinct root cause, confirmed).
- **024:** project ownership & PA write-policy.
- **025:** worktree crash-recovery / orphan re-adoption.

## Cross-cutting impact

- **ADR:** 0011 (created, committed with `design.md`).
- **Glossary:** Session pinning term + PA-Maestro relationships
  (committed during the grill).
- **Reference docs:** none — `DEPLOYMENT.md` unaffected (deploy
  procedure unchanged).
- **INDEX:** row carries issue range #92–#95; flip to *done* when all
  four merge and the live smoke passes.
