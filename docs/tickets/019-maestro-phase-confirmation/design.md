# Design — Ticket 019: Maestro phase-confirmation gate

Chosen approach, resolved from the grill (8 decisions) and hardened by a 4-lens
adversarial red-team against the code. The ADR-consistency lens came back
**solid**: this design keeps Hive content-dumb (ADR 0018), reuses the maestro/
lead permission split (ADR 0010), and introduces no Hive-side helper LLM (the
thing ADR 0013 retired). ADR 0018 lines 62-65 explicitly pre-authorize this
ticket. The decision itself is recorded in **ADR 0019**.

## The approach in one line

A maestro **cannot execute a `spawn_team` action until it has completed one
`request_decision`→user→reply round-trip** — a durable, code-enforced floor —
on top of which the maestro's own JD interprets your reply (go / no / question).

## Two layers (mapping onto ADR 0018's "dumb Hive, smart maestro")

```
  LAYER 1 — Hive, dumb, HARD floor
    block spawn_team until confirmed_with_user == True.
    Guarantees: a maestro never spends without asking you once.
    Hive enforces this without reading your reply — it only knows "a reply happened".

  LAYER 2 — Maestro, smart, SOFT
    role-maestro.md tells the maestro to read your reply and only spawn on a
    clear go; on a question/redirect/no it idles + re-asks (re-parks). Hive does
    not police this (it can't — content-dumb).
```

**Honest residue (accepted, documented in ADR 0019):** the floor proves the
maestro *asked*, not that you *approved*. "Obey the no" is Layer 2 — the
maestro's job. Closing it in code would require Hive to read your replies, which
reverses ADR 0018; that is explicitly out of scope (a possible future "decision
verifier" follow-up, not 019).

## Mechanism — the exact seams

Two new durable boolean fields on the `Entity` dataclass (`models/entity.py`),
persisted exactly like `awaiting_decision` (Ticket 029):

| Field | Default | Meaning |
|-------|---------|---------|
| `confirmed_with_user` | `False` | this entity has completed ≥1 user decision round-trip |
| `phase_confirm` | `True` | the gate is active for this entity (opt-out = `False`) |

Three code touch-points:

1. **SET** — in `clear_awaiting_decision()` (`process/manager.py`, ~214-228),
   which is reached **only** from the user-reply path
   (`commands/dispatch.py` `_send_to_entity`, ~645). When it clears a maestro's
   `awaiting_decision`, also set `confirmed_with_user = True` and persist. Guard
   on *was-parked*: only set it when `awaiting_decision` was `True` (a real
   round-trip), and only for `role == "maestro"`. Add an audit event
   (`entity.phase_confirmation_cleared`, actor `user`).

2. **CHECK** — in the `spawn_team` action handler (`process/message_dispatcher.py`,
   ~545-595), immediately after the existing `can_spawn_team(role)` check:

   ```
   if entity.role == "maestro" and entity.phase_confirm and not entity.confirmed_with_user:
       # deny: audit spawn_team_denied (reason="phase_not_confirmed")
       # route a system corrective note back to the maestro:
       #   "spawn blocked — emit request_decision{to:user} and get a reply first"
       continue
   ```

   The check fires **before** the spawn executes, so it is correct for *both*
   action orderings (`[request_decision, spawn_team]` → spawn dropped by the
   existing `break`; `[spawn_team, request_decision]` → spawn denied here). The
   corrective note reuses the existing system→entity routing pattern (the same
   path other `spawn_team_denied` feedback uses) — it is a fixed string, **not**
   reply interpretation, so content-dumbness holds.

3. **SOURCE `phase_confirm`** — parse an optional `**Phase Confirm**:` field in
   the personality file into `PersonalityConfig` (`entity.py` ~57-124), applied
   via `load_personality` (~258-269). Absent → default `True`. This is the
   per-maestro override (set `off` for an unattended/autonomous maestro). The
   field lives on `Entity` so persistence is uniform; only the maestro gate reads
   it.

### Persistence + migration (`src/hive/bus/migrations/`)

Next free number is **031** at time of writing — **re-check at build** (020 is
in a parallel worktree and may take it; ADR/migration numbers race here). Follow
the `029_entity_awaiting_decision.sql` pattern (`ADD COLUMN IF NOT EXISTS …
DEFAULT …`) and wire both columns into `entity_store.py` upsert + `_row_to_entity`
restore:

```sql
ALTER TABLE entities ADD COLUMN IF NOT EXISTS confirmed_with_user BOOLEAN NOT NULL DEFAULT FALSE;
ALTER TABLE entities ADD COLUMN IF NOT EXISTS phase_confirm       BOOLEAN NOT NULL DEFAULT TRUE;
-- BACKFILL: grandfather every maestro that already exists (see below)
UPDATE entities SET confirmed_with_user = TRUE WHERE role = 'maestro';
```

**Backfill decision (grandfather-all).** Without a backfill, deploying 019 sets
`confirmed_with_user = False` on every live maestro, so each one's *next*
`spawn_team` is blocked until it re-asks — a surprising, fleet-wide behavior
change. We grandfather **all** existing maestros (`WHERE role='maestro'`), so 019
only gates maestros **created after deploy**. The stricter alternative (only
grandfather maestros that already own a team, leaving a pre-019 *teamless*
maestro gated) was rejected: marginal benefit, and it changes behavior for an
already-running maestro. The accepted cost: a pre-019 teamless maestro escapes
the gate permanently — a tiny, one-time transitional gap.

> Consequence worth naming: the established **otter** (which exists pre-019) is
> grandfathered, so it won't gate on its next spawn after deploy. otter gates
> "like everyone" (grill Q5) only when a *fresh* otter is created — consistent
> with 019 being a **creation-time** checkpoint.

## The two spawn paths — what's gated, what's exempt

```
  team creation has exactly two entry points:

  (1) maestro emits spawn_team action      → AUTONOMOUS → GATED  (the new check)
      message_dispatcher spawn_team handler

  (2) user types /team create <name>        → HUMAN-INITIATED → EXEMPT
      dispatch.py _execute_team → create_team(default_maestro, …)
```

`/team create` is **deliberately exempt**: the human typing the command *is* the
confirmation. The gate exists to stop a maestro spending **autonomously** without
asking — not to second-guess an explicit human command. (This is the only other
spawn path; the red-team confirmed there is no third.)

## Reject / non-go behavior (Layer 2, in the JD)

Resolved in grill Q3: on anything that isn't a clear go, the maestro **idles and
re-asks** — it emits a fresh `request_decision` (which re-parks it) and 029's
~hourly nudge keeps re-surfacing it. **Never auto-kill, never go silent** (matches
the existing JD rule "Never go silent on the user"). A hard "no" → it acknowledges
and holds (parked), waiting for your next instruction; killing it stays *your*
call (`/kill`). No code change — this is JD wording (see Reference-doc impact).

## Alternatives considered (and why rejected)

- **A · JD-only (soft).** Just sharpen role-maestro.md; no code. Rejected: a gate
  the model can skip is a suggestion, not a guarantee — exactly what fails under
  AFK autonomy, which is 019's whole reason to exist.
- **Verifier / LLM-as-judge on the reply (hard "obey the no").** A judge that
  reads your reply and only unlocks on a real go. Rejected *for 019*: it reverses
  ADR 0018's content-dumb decision (made 2 days prior), is a decision-*channel*
  feature (affects every `request_decision`, not just spawn), re-introduces a
  Hive-side helper LLM (against ADR 0013), and only relocates trust (the judge is
  itself fallible). Captured as a possible future follow-up, not 019.
- **2b · labeled plan-approval round-trip.** Require the maestro to tag its
  question as "plan approval" so only that counts. Rejected: the tag is
  model-emitted, so it smuggles model-trust back into the hard gate we chose B to
  escape.
- **PA (otter) exempt by default.** Rejected per grill Q5 — otter gates like
  everyone; the `phase_confirm` flag is the only override.

## Reference-doc impact (declared up front — cross-cutting)

- **`personalities/role-maestro.md`** — add Layer-2 wording: treat anything that
  isn't a clear go (including a question back) as "keep asking, don't spawn"; if a
  spawn is denied, ask the user first; document `**Phase Confirm**: off` for
  unattended runs. (Behavior, eyeballed in the deployed re-smoke.)
- **`CONTEXT.md`** — new glossary term **Phase-confirmation gate** (added with
  this design; see below).
- **`docs/adr/0019-maestro-phase-confirmation-gate.md`** — new decision (this
  design); builds on ADR 0018. Re-check the ADR number at ship (races with 020).

## Acceptance (refined from ticket.md)

- A freshly-created maestro (`confirmed_with_user=False`, `phase_confirm=True`)
  that emits `spawn_team` is **denied** and told to ask first; after a
  `request_decision`→reply round-trip it spawns. Verified on deployed code.
- The flag is durable: a restart mid-park restores `awaiting_decision` and
  `confirmed_with_user`; the maestro neither double-asks nor escapes the gate.
- `phase_confirm=False` (personality opt-out) skips the gate — an unattended
  maestro spawns without parking.
- Leads are unaffected (they can't `spawn_team` or `request_decision` to user).
- `/team create` still works (human-initiated, exempt).
- Backfill: existing maestros are not retroactively gated.
- `ruff` + `pytest -m "not integration"` green; deployed maestro→lead→leaf→user
  round-trip with the gate bridged.
