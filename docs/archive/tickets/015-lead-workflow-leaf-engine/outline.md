# 015 — Outline (implementation structure)

Single PR (DIRECT lane). Approach in [`design.md`](design.md); decision in
[ADR 0010](../../adr/0010-leads-orchestrate-via-workflow.md). Five independent
seams; commit order is bottom-up so each commit is green on its own.

## Seam map

```
c1 FENCING          tool_policy.role_tool_denylist  ──merged at──▶ _adapter_config_from_entity
   (Q2a/b/c)        _render_auto_personality stops writing "## Tools"

c2 WORKTREE FLOOR   TeamLead.worktree_path  ◀─created in─ create_team (worktree_mgr.create)
   (Q3)             _get_or_create_adapter cwd  ◀── includes leads, not just Workers
                    kill_entity ── removes the lead worktree (worktree_mgr.remove)

c3 IDLE-KILL        ClaudeAdapter.is_busy() = self._lock.locked()
   (Q4)             kill_idle_entities ── skip when adapter.is_busy()  (alongside GATED)

c4 READER HARDEN    transcript_reader.await_next_assistant_turn:
   (Q6)               • don't accept while last entry has an unresolved tool_use
                        (reuse gates._tool_use_blocks / _resolved_tool_use_ids)
                      • 180s timeout → no-progress timer (reset on transcript move / pending tool)

c5 JD REFRAME       personalities/role-lead.md edited IN PLACE (keep 3 prompt blocks)
   (Q5)             Workflow-as-fan-out; demote spawn_worker; reframe Agent/Task ban
```

## c1 — Fencing (policy in code)

- **New** `src/hive/process/tool_policy.py`: pure `role_tool_denylist(role: str)
  -> list[str]` returning the lead/maestro/worker deny tokens per the design
  table. No I/O, fully unit-testable.
- **`lifecycle_manager._adapter_config_from_entity`**: merge order becomes
  `entity.disallowed_tools + role_tool_denylist(role) + skill_denylist_for(role)`,
  de-duped first-seen (the existing `dict.fromkeys` pattern). This is the only
  authoritative source now, and it runs on **every** spawn (closes the restart
  hole).
- **`lifecycle_manager._render_auto_personality`**: delete the `## Tools` block
  for maestro/lead (policy no longer lives in markdown). Personality-file
  `## Tools` parsing stays as a per-Entity override.
- Tests: `tests/process/test_tool_policy.py` (new); update
  `test_render_auto_personality_locks_tools_for_coordinators` → assert the
  function, not the markdown.

## c2 — Worktree floor

- **`models/team_lead.py`**: add `worktree_path: str | None = None`.
- **`lifecycle_manager.create_team`**: if `worktree_mgr` configured,
  `await worktree_mgr.create(lead_name, branch=f"hive/{lead_name}")` and store on
  the lead (mirrors `spawn_worker`).
- **`lifecycle_manager._get_or_create_adapter`**: cwd selection includes leads —
  `cwd = Path(entity.worktree_path)` for a `TeamLead` with a path too, not only
  `Worker`.
- **`lifecycle_manager.kill_entity`**: the TeamLead branch removes the worktree
  (`worktree_mgr.remove(lead_name)`), mirroring the Worker cleanup.
- Tests: lead spawns with a worktree cwd; kill removes it.
- **Handoff to 016:** cleanup of *changed* leaf-agent worktrees (commit→PR→merge)
  is out of scope here.

## c3 — Idle-kill exemption

- **`ClaudeAdapter`**: add `def is_busy(self) -> bool: return self._lock.locked()`.
- **`lifecycle_manager.kill_idle_entities`** (~:586-615): after the `GATED`
  check, also `continue` if the entity's adapter exists and `is_busy()` — never
  reap a turn in flight.
- Tests: an entity with a busy adapter is not killed despite a stale
  `last_activity_at`.

## c4 — Reader hardening (required by sync-wait)

- **`transcript_reader.await_next_assistant_turn`**: before accepting (after the
  count + mtime-quiescence gate, before `_extract_last_turn`), reject if the last
  assistant entry contains a `tool_use` with no matching `tool_result` — i.e. a
  pending tool call (a blocking `TaskOutput`). Reuse the pairing helpers in
  `runtime/gates.py` (`_tool_use_blocks`, `_resolved_tool_use_ids`); lift them to
  a shared spot if cleaner.
- **Timeout**: replace the flat 180s wall-clock with a **no-progress** timer —
  reset the deadline whenever the transcript mtime advances or a tool_use is
  pending. A long fan-out no longer trips it; a genuinely hung session still does.
- Tests: synthetic transcript with a pending `TaskOutput` tool_use → reader does
  **not** accept; once the `tool_result` lands → it accepts and extracts the real
  final turn. (Guards the mis-attribution race from research §"Turn model".)

## c5 — Lead JD reframe

- **`personalities/role-lead.md`**, edited in place (no new
  `--append-system-prompt` block): rewrite identity + workflow sections so
  **Workflow is the leaf-execution path**; reframe the Agent/Task ban into
  "fan out via `Workflow`, not raw `Agent`/`Task`"; instruct
  `isolation:'worktree'` for file-mutating agents; **demote** `spawn_worker`
  (kept working, no longer instructed).
- Tests: `test_role_jd.py` lead assertions updated (Workflow guidance present);
  `test_entity.py` lead block still counts **3** prompt blocks.

## Verification gate (every commit)

`ruff check src/ tests/ && ruff format --check src/ tests/` (separate gates) +
`pytest -m "not integration"` green. Full picture in [`plan.md`](plan.md).
