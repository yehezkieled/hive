# Outline — Ticket 019: Maestro phase-confirmation gate

Implementation structure, in build order. One cohesive change (DIRECT lane) —
the gate is inert until every piece lands, so it ships as one PR. File refs are
symbol-anchored (line numbers drift); the implementer re-confirms.

## Build order

1. **Migration** — `src/hive/bus/migrations/0NN_entity_phase_confirmation.sql`
   (next free number; **031 at time of writing — re-check, 020 races**). Follow
   the `029_entity_awaiting_decision.sql` shape:
   ```sql
   ALTER TABLE entities ADD COLUMN IF NOT EXISTS confirmed_with_user BOOLEAN NOT NULL DEFAULT FALSE;
   ALTER TABLE entities ADD COLUMN IF NOT EXISTS phase_confirm       BOOLEAN NOT NULL DEFAULT TRUE;
   UPDATE entities SET confirmed_with_user = TRUE WHERE role = 'maestro';  -- grandfather existing
   ```

2. **Entity model** — `src/hive/models/entity.py`. Add two fields beside
   `awaiting_decision` (~222): `confirmed_with_user: bool = False`,
   `phase_confirm: bool = True`.

3. **Persistence** — `src/hive/bus/entity_store.py`. Wire both columns into the
   `upsert` INSERT/UPDATE (~33-80) and `_row_to_entity` restore (~125-165),
   mirroring `awaiting_decision` exactly.

4. **Personality source for `phase_confirm`** — `src/hive/models/entity.py`.
   Parse an optional `**Phase Confirm**:` field in `parse_personality` (~70-124)
   into `PersonalityConfig` (~57-67); apply in `load_personality` (~258-269).
   Absent → keep default `True`. (`off`/`false` → `False`.)

5. **SET point** — `src/hive/process/manager.py` `clear_awaiting_decision`
   (~214-228). When clearing a maestro that **was** parked
   (`awaiting_decision` True) and `role == "maestro"`: set
   `confirmed_with_user = True`, persist, and audit
   `entity.phase_confirmation_cleared` (actor `user`).

6. **CHECK point** — `src/hive/process/message_dispatcher.py` `spawn_team`
   handler (~545-595), right after `can_spawn_team` (~548):
   ```python
   if entity.role == "maestro" and entity.phase_confirm and not entity.confirmed_with_user:
       # audit spawn_team_denied (reason="phase_not_confirmed")
       # route system corrective note -> entity (reuse existing system->entity path):
       #   "spawn blocked — emit request_decision{to:user} and get a reply first"
       continue
   ```
   Fires before the spawn, so both action orderings are covered. Use
   `entity.role == "maestro"` (no `Maestro` import needed).

7. **Role JD (Layer 2)** — `personalities/role-maestro.md`. Add: treat anything
   that isn't a clear go (incl. a question back) as "keep asking, don't spawn";
   if a spawn is denied, ask the user first; document `**Phase Confirm**: off`
   for unattended runs. Behavior — verify in the deployed re-smoke.

## Tests (`tests/` mirroring `src/`)

- `confirmed_with_user` defaults False; `phase_confirm` defaults True.
- spawn_team **denied** when maestro `confirmed_with_user=False, phase_confirm=True`;
  a corrective note is routed back.
- After a `request_decision`→user-reply round-trip (`clear_awaiting_decision`),
  `confirmed_with_user` flips True and spawn_team **succeeds**.
- `phase_confirm=False` → spawn_team **succeeds** with no round-trip (opt-out).
- Lead emitting spawn_team still denied by role (unchanged).
- Persistence round-trip: upsert→restore preserves both flags (restart safety).
- Backfill migration sets existing maestros `confirmed_with_user=True`.
- `/team create` path still creates a team with no gate (exempt).
- `clear_awaiting_decision` does **not** set the flag when entity wasn't parked.

## Out of scope (no code)

- A reply verifier / LLM-as-judge (the hard "obey the no") — deferred, would
  reverse ADR 0018.
- Gating `/team create` (human-initiated, exempt by design).
