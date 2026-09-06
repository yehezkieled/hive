# 019 — Maestro phase-confirmation gate

## What

Every time a new Maestro is created, it must **pause and ask the human to
confirm before it advances to its next phase of work**. The Maestro asks on
Telegram, ends its Turn, and parks (`awaiting_decision`) until the human
replies — giving the human a steering point at the orchestration layer before
the Maestro commits to executing.

> **Re-mechanized (2026-06-14).** 019 was first framed on the native
> interactive-gate bridge (Ticket 003). It now rides Ticket
> [029](../029-maestro-gate-bridge-regression/)'s **conversational decision
> channel** ([ADR 0018](../../adr/0018-conversational-decision-channel.md)) —
> `request_decision{to:user}` + `awaiting_decision`, no native gate. This
> resolves 019's old "native gate vs hive_action" mechanism question in favour
> of the action.

The first and most important checkpoint is at **creation**: a freshly spawned
Maestro that has framed a plan should not roll straight into execution
(spawning Teams, fanning out a Workflow, spending plan quota) without a human
"go".

## Why

As Maestros gain autonomy — Workflow-native fan-out (S5), advisor escalation
(013) — the human wants a deliberate approval point before a Maestro acts on a
plan. A misframed plan caught at the gate costs nothing; the same plan executed
spends quota, spawns a fleet, and may need unwinding. This is human-in-the-loop
oversight where it is cheapest: at the phase boundary, before the spend.

It rides Hive's **conversational decision channel** (Ticket
[029](../029-maestro-gate-bridge-regression/), [ADR
0018](../../adr/0018-conversational-decision-channel.md)) rather than inventing
a new path: the Maestro emits `request_decision{ to:"user", text:… }` at the
phase boundary, which routes to Telegram and ends the Turn with
`awaiting_decision` set; the scheduler skips the parked Maestro until you reply.
A phase confirmation is that channel fired at a Maestro phase transition — not a
native gate (those are retired for Maestros by 029).

## Acceptance

- Creating a Maestro results in a confirmation surfaced to the human (Telegram)
  **before** the Maestro advances past its first phase; the Maestro ends its
  Turn with `awaiting_decision` set and the scheduler does not advance it until
  the human replies.
- On approve → the Maestro proceeds; on reject/redirect → the Maestro reads the
  free-text reply and halts or re-plans (exact semantics resolved in design).
- The confirmation rides Ticket 029's conversational decision channel
  (`request_decision{to:user}` + `awaiting_decision`), not a native gate or a
  bespoke channel.
- Only **Maestros** use this — Leads are unaffected.
- `ruff` + `pytest -m "not integration"` green; a maestro turn completes
  end-to-end with the confirmation in the loop.

## Open design questions (resolve in research/design)

- **What is a "phase"?** A fixed Maestro lifecycle (created → planning →
  executing → done), or Maestro-declared phase boundaries? The mechanism
  differs — a fixed transition Hive detects vs. an action the Maestro emits.
- **Only at creation, or every phase boundary?** The ask names creation
  ("everytime I create a new maestro"); decide whether later phase transitions
  also gate or just the first.
- **Mechanism.** ✅ Resolved by 029 / ADR 0018 — the `hive_actions`
  (`request_decision{to:user}` + `awaiting_decision`) path, not a native gate.
- **Default + opt-out.** On for all Maestros by default (the ask implies yes);
  is a per-Maestro or per-session opt-out needed for trusted autonomous runs?
- **Reject semantics.** Does a "no" kill the Maestro, send it back to re-plan,
  or just record dissent and continue? (029 Q4 makes Hive content-dumb — the
  Maestro reads the free-text reply and decides; 019 defines what "halt" means.)
- **Timeout / AFK.** ✅ Resolved by 029 / ADR 0018 — park forever
  (`awaiting_decision`), re-ping via the reused ~hourly nudge, never auto-decide.
  The flag is durable, so a restart can't poke the Maestro into acting.

## Non-goals

- Phase confirmations for **Leads** (they escalate to a parent, not the human —
  a wait there would stall).
- Mid-phase, free-form steering of a running Maestro (separate concern; this is
  a discrete boundary confirmation, not continuous control).
- Building the decision channel itself — Ticket 029 owns that
  (`request_decision{to:user}` + `awaiting_decision`); 019 consumes it.

## Cross-cutting

Likely touches the Maestro role JD (`personalities/role-maestro.md`) and the
process loop that drives a Maestro Turn. Depends on Ticket 029 (the channel).
Declare reference-doc impact in `plan.md`.

## Sprint

Candidate for **S6** — S5 is already a heavy slice (its own risk note flags the
overrun), and 019 is a new feature orthogonal to S5's Workflow-engine goal.
Tracked as `planned` until a sprint commits it.
