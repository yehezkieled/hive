# 0019 — A maestro's first team spawn is code-gated on a user confirmation

**Status:** Accepted (2026-06-16)
**Ticket:** [019](../tickets/019-maestro-phase-confirmation/)
**Relates to:** [ADR 0018](0018-conversational-decision-channel.md) (the
content-dumb decision channel this fires at a phase boundary — builds on, does
not amend), [ADR 0010](0010-leads-orchestrate-via-workflow.md) (the maestro/lead
tool split this reuses), [ADR 0013](0013-retire-custom-advisor.md) (no Hive-side
helper LLM — this honours it)

> Re-check this ADR number at ship — Ticket 020 is in a parallel worktree and the
> numbers race.

## Context

A freshly-created maestro frames a plan and then spends: it spawns teams (each a
Lead = a PTY = plan quota) and fans out work. Ticket 019 wants a human checkpoint
*before* that first spend — oversight where it is cheapest, before a misframed
plan executes.

The maestro JD (since Ticket 029, ADR 0018) **already tells** a maestro to ask
the user via `request_decision{to:user}` before spawning teams, and the
conversational decision channel already parks it while it waits. But that is pure
guidance: the `spawn_team` action handler gates only on role
(`can_spawn_team`) — **nothing verifies the maestro actually asked.** A maestro
that ignores its JD spawns teams and starts spending, and no code stops it. So
the real question 019 answers is not "build a gate" but **how strong the
guarantee should be.**

A hard guarantee runs into ADR 0018's deliberate constraint: **Hive is
content-dumb** — it does not read the user's reply, so it cannot tell "go" from
"no". Whatever we build cannot enforce "spawn only after approval"; it can only
enforce things Hive observes without interpreting.

## Decision

**A maestro cannot execute a `spawn_team` action until it has completed at least
one `request_decision`→user→reply round-trip.** This is a code-enforced floor,
not JD guidance.

1. **Hard floor (Hive, dumb).** A durable `confirmed_with_user` flag (default
   `False`) is set when a user reply clears the maestro's `awaiting_decision`.
   The `spawn_team` handler denies the action while the flag is `False`, and
   feeds back a fixed corrective note ("ask the user first"). The flag means *"a
   reply happened"*, never *"the user approved"* — so Hive stays content-dumb.
2. **Soft layer (maestro, smart).** The JD tells the maestro to read the reply
   and only spawn on a clear go; on a question / redirect / no it idles and
   re-asks (re-parking via 029's channel + nudge), never auto-killing, never
   going silent. Hive does not police this.
3. **Per-maestro opt-out.** A `phase_confirm` flag (default `True`, set via a
   `**Phase Confirm**:` personality field) disables the gate for an unattended /
   autonomous maestro — otherwise such a maestro, with no human to reply, would
   park forever and never spawn. Default `True` for **all** maestros, including
   the PA (otter).
4. **Only autonomous spawns are gated.** A maestro `spawn_team` *action* is
   gated; a user-typed `/team create` is **exempt** — the human issuing the
   command is the confirmation.
5. **Grandfather existing maestros.** The migration backfills
   `confirmed_with_user = TRUE` for every maestro that exists at deploy, so 019
   gates only maestros created afterward — no retroactive fleet-wide block.

## Alternatives rejected

- **JD-only (soft).** Sharpen the JD, no code. A gate the model can skip is a
  suggestion, not a guarantee — exactly what fails under AFK autonomy.
- **A reply verifier / LLM-as-judge (the hard "obey the no").** Have Hive judge
  whether your reply is a real go before unlocking. Rejected for 019: it reverses
  ADR 0018's content-dumb decision, is a decision-*channel* feature (affects every
  `request_decision`), re-introduces a Hive-side helper LLM (against ADR 0013),
  and only relocates trust (the judge is itself fallible). A possible future
  follow-up, not 019.
- **Labeled plan-approval round-trip.** Count only a question the maestro *tags*
  as plan-approval. Rejected — the tag is model-emitted, smuggling model-trust
  back into the hard gate.
- **PA (otter) exempt by default.** Rejected — otter gates like everyone; the
  `phase_confirm` flag is the only override.

## Consequences

- **The guarantee is "asked", not "approved."** The floor proves a human was in
  the loop before any spend; obeying a "no" remains the maestro's job (Layer 2).
  Closing that residue needs Hive to read replies — out of scope, and it would
  reverse ADR 0018.
- Two persisted entity fields (`confirmed_with_user`, `phase_confirm`) + a
  migration with a backfill; restart re-adoption of the flags aligns with Ticket
  025's crash-recovery theme.
- Behaviour change on live maestros is limited to those created after deploy
  (grandfather backfill); a pre-019 *teamless* maestro escapes the gate
  permanently — an accepted one-time transitional gap.
- The gate is a creation-time checkpoint: it fires once per maestro (the first
  spawn), then never again. The established otter, grandfathered, gates only when
  a fresh otter is created.
- New cross-cutting edit to `personalities/role-maestro.md` (Layer-2 wording +
  the opt-out field) and a new `CONTEXT.md` glossary term.
