# Plan — Ticket 015: Lead leaf engine (orchestrate via CC Workflow)

FAN-OUT lane (converted from direct at build time — the owner opted for a
PRD + issues + fleet build). Approach in [`design.md`](design.md); seam map in
[`outline.md`](outline.md); decision in
[ADR 0010](../../adr/0010-leads-orchestrate-via-workflow.md); PRD in
issue [#74](https://github.com/yehezkieled/hive/issues/74).

**Dependency:** none blocking (015 is the S5 foundation). Assumes CC ≥ 2.1.101
on the fleet (Ticket 009 — pinned 2.1.170, verified).

## Slices

| Summary (what the slice delivers) | Issue | Type | Blocked by |
|-----------------------------------|-------|------|------------|
| Role tool-policy in code: prune lead `TaskOutput`/`TaskStop`, deny maestro `Workflow`, close both guard holes | [#75](https://github.com/yehezkieled/hive/issues/75) | AFK | — |
| Worktree floor: every Team Lead spawns in its own worktree | [#76](https://github.com/yehezkieled/hive/issues/76) | AFK | — |
| Idle-reaper exempts Entities with a Turn in flight | [#77](https://github.com/yehezkieled/hive/issues/77) | AFK | — |
| Transcript reader: pending-tool accept-guard + no-progress timeout | [#78](https://github.com/yehezkieled/hive/issues/78) | AFK | — |
| Lead JD reframe: Workflow is the leaf-execution path | [#79](https://github.com/yehezkieled/hive/issues/79) | AFK | #75 |

## Execution waves

- Wave 1: #75, #76, #77, #78 (parallel — #75/#76/#77 overlap on
  `lifecycle_manager.py`, which is merge-order territory, not a logical blocker)
- Wave 2: #79

## Conventions

- Branch `ticket-015/issue-<n>-<slug>`, target `main`, squash-merge
- Validation gate (every PR): `ruff check src/ tests/ && ruff format --check
  src/ tests/` + `pytest -m "not integration"` (75% coverage floor)
- Autonomy: all AFK — auto-merge on green CI (owner preapproved this run)

To build: run the fleet Workflow against this `plan.md`.

## Files this Ticket creates / modifies

| Path | Op | Step |
|------|----|------|
| `src/hive/process/tool_policy.py` | **create** — `role_tool_denylist(role)` | c1 |
| `src/hive/process/lifecycle_manager.py` | modify — merge `role_tool_denylist` in `_adapter_config_from_entity`; drop `## Tools` from `_render_auto_personality`; lead worktree in `create_team` + `kill_entity`; lead cwd in `_get_or_create_adapter`; `is_busy` skip in `kill_idle_entities` | c1,c2,c3 |
| `src/hive/models/team_lead.py` | modify — add `worktree_path: str \| None` | c2 |
| `src/hive/runtime/claude_adapter.py` | modify — add `is_busy()` | c3 |
| `src/hive/runtime/transcript_reader.py` | modify — pending-`tool_use` accept-guard; no-progress timeout | c4 |
| `src/hive/runtime/gates.py` | modify (maybe) — expose tool_use/tool_result pairing helpers for reuse | c4 |
| `personalities/role-lead.md` | modify — Workflow-as-fan-out; demote spawn_worker; reframe ban (in place) | c5 |
| `tests/process/test_tool_policy.py` | **create** — lead pruned of TaskOutput/TaskStop; maestro denied Workflow; Agent/Task denied both | c1 |
| `tests/process/test_lifecycle_manager.py` | modify — retarget the locks-tools test to `role_tool_denylist`; no `## Tools` in auto-personality; lead spawns with worktree cwd + kill removes it; busy adapter not reaped | c1,c2,c3 |
| `tests/runtime/test_transcript_reader.py` | modify/create — pending `TaskOutput` → no accept; resolved → accept real final turn | c4 |
| `tests/test_role_jd.py` | modify — lead JD asserts Workflow guidance | c5 |
| `tests/test_entity.py` | modify — lead JD content updated; still **3** prompt blocks | c5 |
| `docs/adr/0010-leads-orchestrate-via-workflow.md` | created (committed) | — |

## Verification

### Hermetic (CI — proves the seam)
- `pytest tests/process/test_tool_policy.py` — `role_tool_denylist("lead")` has
  **no** `TaskOutput`/`TaskStop`, **keeps** `Agent`/`Task`; `("maestro")`
  **includes** `Workflow`.
- A spawned lead's adapter cwd is a **worktree path**, not `None`/main.
- `grep -n "## Tools" ` is gone from `_render_auto_personality` output; the
  guard now comes from `role_tool_denylist` on every spawn.
- A busy-adapter entity with a stale `last_activity_at` is **not** killed.
- Transcript reader does **not** accept while the last entry has an unresolved
  `tool_use`; accepts the real final turn once resolved.
- `role-lead.md` contains Workflow fan-out guidance; `test_entity.py` lead block
  count still **== 3**.
- `ruff check src/ tests/` **and** `ruff format --check src/ tests/` (separate
  gates); full `pytest -m "not integration"` green.

### Live smoke (deployed — NOT in CI, scheduled for the evening of 2026-06-11)
- `git push`; `systemctl --user restart hive.service`; `journalctl --user -u
  hive.service -n 20`.
- A maestro → lead turn where the **lead runs an actual `Workflow` fan-out**
  (e.g. 2 agents, each touching a separate file) and returns one synthesized
  report. Verify from the Tailscale IP, not loopback. This is the only proof of
  the fan-out itself (the engine inside the lead's CC session can't be mocked
  hermetically).

## Out of scope (handoffs)

- **016:** migrate `spawn_worker` → Workflow; remove `spawn_worker` from the
  lead + maestro JDs; cleanup of **changed** leaf-agent worktrees
  (commit→PR→merge→remove).
- **017:** the read-only progress watcher → dashboard + Telegram.
- **018:** delete the persistent Worker entity; worker JD; redefine/retire the
  "Worker" glossary term.
- **S6:** mid-run steering of a running Workflow; the usage-undercount fix.

## Cross-cutting impact

- **ADR:** 0010 (created).
- **Reference docs:** none required by 015. (`docs/DEPLOYMENT.md` mentions the
  advisor, not the lead guard; no change here.)
- **Glossary:** no `CONTEXT.md` change in 015 — "Turn" is preserved by the
  sync-wait choice; "Worker" is redefined/retired in 018.
- **INDEX:** row already flipped to *in progress*; mark *done* at merge.
