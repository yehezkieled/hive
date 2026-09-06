# Plan — Ticket 019: Maestro phase-confirmation gate  (issue #179)

DIRECT lane — one cohesive change, one PR that closes #179. The gate is inert
until all pieces land together, so it is not worth slicing. Full spec lives in
`design.md` / `outline.md` / [ADR 0019](../../adr/0019-maestro-phase-confirmation-gate.md);
this is the file-operation contract.

## Files this Ticket creates / modifies

| Path | Op | Step |
|------|----|------|
| `src/hive/bus/migrations/0NN_entity_phase_confirmation.sql` | create | add `confirmed_with_user`(False) + `phase_confirm`(True) columns; backfill `confirmed_with_user=TRUE WHERE role='maestro'`. **Re-check number (031 now; 020 races)** |
| `src/hive/models/entity.py` | modify | add `confirmed_with_user`/`phase_confirm` fields; parse `**Phase Confirm**:` in `parse_personality` + `PersonalityConfig`; apply in `load_personality` |
| `src/hive/bus/entity_store.py` | modify | wire both columns into `upsert` + `_row_to_entity` (mirror `awaiting_decision`) |
| `src/hive/process/manager.py` | modify | in `clear_awaiting_decision`: set `confirmed_with_user=True` for a maestro that was parked; persist; audit `entity.phase_confirmation_cleared` |
| `src/hive/process/message_dispatcher.py` | modify | in `spawn_team` handler after `can_spawn_team`: deny + corrective system note when `role=="maestro" and phase_confirm and not confirmed_with_user` |
| `personalities/role-maestro.md` | modify | Layer-2 JD wording (non-go → keep asking, don't spawn; if denied, ask first; document `**Phase Confirm**: off`) |
| `tests/.../test_phase_confirmation*.py` | create | the cases in `outline.md` (deny, round-trip flip, opt-out, persistence, backfill, `/team create` exempt, lead unaffected) |

## Verification

- `ruff check src/ tests/ && ruff format --check src/ tests/`
- `pytest -m "not integration"` green (new phase-confirmation tests included)
- **Deployed re-smoke** (behavior, not just unit): a fresh maestro emits
  `spawn_team` → denied + told to ask → emits `request_decision{to:user}` →
  parks → user reply → flips `confirmed_with_user` → spawn succeeds; restart
  mid-park preserves both flags; `phase_confirm=off` maestro spawns with no
  round-trip.

## Out of scope

- A reply verifier / LLM-as-judge (the hard "obey the no") — deferred (would
  reverse ADR 0018); a possible future decision-channel follow-up.
- Gating `/team create` (human-initiated, exempt by design).

## Cross-cutting impact (declared up front)

- `personalities/role-maestro.md` — Layer-2 wording + opt-out field (in this PR).
- `CONTEXT.md` — glossary term **Phase-confirmation gate** (already added in the
  design commit).
- `docs/adr/0019-maestro-phase-confirmation-gate.md` — new decision (already
  added). **Re-check ADR number at ship** (020 races).
