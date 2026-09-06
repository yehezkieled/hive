# Plan — Ticket 046: scheduler poke prose fix  (issue #233)

**Lane:** direct — one branch, one PR that closes #233. Trivial (one prompt
reword + a test). Sprint 2026-Q2-S9.

## Files this Ticket creates / modifies

| Path | Op | Step |
|------|----|------|
| `src/hive/process/scheduler.py` | modify | `build_facts_prompt`: reword the decision poke — emit a block only when a change is needed; when idle, **do NOT** emit a block, reply in plain prose. |
| `tests/test_scheduler.py` | modify | `test_facts_prompt_no_action_path_uses_prose_not_block` — asserts the prose steer + that the old ambiguous juxtaposition is gone. |

No new source files. No parser change (deliberate — see `ticket.md`).

## Verification

- `pytest tests/test_scheduler.py` green (21).
- `ruff check src/ tests/ && ruff format --check src/ tests/` green.
- Full `pytest -m "not integration"` green.
- **Deployed re-smoke:** restart `hive.service`; an idle maestro's next scheduler
  poke produces a plain-prose reply (no `<hive_actions>` block, no red parse error
  in chat).

## Out of scope

- `parse_actions` leniency · the `<hive_actions>` protocol · chat/decision channels.

## Cross-cutting impact

- None. No CONTEXT/ADR/DEPLOYMENT change.
