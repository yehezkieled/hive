# Plan — Ticket 012: Curate & expose CC skills to Entities

Direct lane — one PR, one branch. Builds the [design](design.md) /
[outline](outline.md). Decision recorded in
[ADR 0008](../../adr/0008-per-role-skill-curation-denylist.md).

## Files this Ticket creates / modifies

| Path | Op | Step |
|------|----|------|
| `src/hive/process/skill_curation.py` | create | role→`Skill()` deny tokens + `skill_denylist_for(role)` |
| `src/hive/process/lifecycle_manager.py` | modify | merge `skill_denylist_for(entity.role)` into `disallowed_tools` at `_adapter_config_from_entity()` (~line 109) |
| `personalities/role-maestro.md` | modify | add `## Skills — when to use` (autonomous **+** thinking set) |
| `personalities/role-lead.md` | modify | add `## Skills — when to use` (autonomous set) |
| `personalities/role-worker.md` | modify | add `## Skills — when to use` (autonomous set) |
| `tests/process/test_skill_curation.py` | create | per-role denylist unit tests (incl. Maestro excludes thinking) |
| `tests/runtime/test_claude_adapter.py` *(or seam test)* | modify | assert spawned args carry/omit `Skill(...)` tokens per role |

## Verification

- `pytest tests/process/test_skill_curation.py tests/runtime -q` green.
- Full gate: `ruff check src/ tests/ && ruff format --check src/ tests/ && pytest -m "not integration"`.
- Build-time: confirm the `brainstorming` deny token (`Skill(brainstorming)`
  vs `Skill(superpowers:brainstorming)`) on the **pinned** binary
  (`HIVE_CLAUDE_BINARY`); use whichever the binary honours.
- Host smoke (deployed): spawn a Worker; confirm `grill-me` is unreachable
  and `tdd` invokes; spawn/confirm a Maestro still reaches `grill-me`.

## Out of scope

- Per-spawn config-dir isolation (`CLAUDE_CONFIG_DIR`) — rejected (ADR 0008).
- Filesystem trimming of the installed skill library — **Ticket 014**.
- Any change to the gate bridge (Ticket 003) or the permission-mode flags.
- A true allowlist — not supported by Claude Code (ADR 0008).

## Cross-cutting impact

- Reference docs: **none** required. (`CONTEXT.md` gains the "thinking
  skill" glossary term as part of this Ticket's grill; `DEPLOYMENT.md`
  unaffected — no systemd/runbook change.)
- ADR 0008 added (append-only).

## Follow-up

- After Ticket 014 trims the VPS skill library, revisit
  `skill_curation.py` to drop tokens for any now-deleted skills.
