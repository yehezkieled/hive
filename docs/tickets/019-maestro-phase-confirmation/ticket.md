# 019 — Maestro phase-confirmation gate

## What

Every time a new Maestro is created, it must **pause and ask the human to
confirm before it advances to its next phase of work**. The Maestro blocks
on that confirmation (Telegram) and only proceeds once the human approves —
giving the human a steering point at the orchestration layer before the
Maestro commits to executing.

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

It reuses Hive's existing **Interactive gate** machinery rather than inventing a
new path — Ticket [003](../003-plan-interaction-bridge/) already bridges a
mid-Turn gate (`ExitPlanMode` / `AskUserQuestion` / STOP checkpoint) on the PTY
Harness to the user on Telegram (hold-and-inject). A phase confirmation is that
same gate, fired at a Maestro phase transition.

## Acceptance

- Creating a Maestro results in a confirmation surfaced to the human (Telegram)
  **before** the Maestro advances past its first phase; the Maestro Turn blocks
  until the human answers.
- On approve → the Maestro proceeds; on reject/redirect → the Maestro halts or
  re-plans (exact semantics resolved in design).
- The gate rides the Ticket 003 interactive-gate bridge, not a bespoke channel.
- Only **Maestros** gate this way — Leads and Workers are unaffected.
- `ruff` + `pytest -m "not integration"` green; a maestro turn completes
  end-to-end with the gate in the loop.

## Open design questions (resolve in research/design)

- **What is a "phase"?** A fixed Maestro lifecycle (created → planning →
  executing → done), or Maestro-declared phase boundaries? The mechanism
  differs — a fixed transition Hive detects vs. an action the Maestro emits.
- **Only at creation, or every phase boundary?** The ask names creation
  ("everytime I create a new maestro"); decide whether later phase transitions
  also gate or just the first.
- **Mechanism.** Reuse 003's gate primitives directly (have the Maestro JD/flow
  hit an `AskUserQuestion`/STOP checkpoint at the boundary), or add an explicit
  `hive_actions` phase-confirm action Hive intercepts? The former is less code;
  the latter is more legible in the org tree.
- **Default + opt-out.** On for all Maestros by default (the ask implies yes);
  is a per-Maestro or per-session opt-out needed for trusted autonomous runs?
- **Reject semantics.** Does a "no" kill the Maestro, send it back to re-plan,
  or just record dissent and continue? What does the human's free-text steer do.
- **Timeout / AFK.** If the human is away, does the gate block indefinitely,
  time out, or fall back to a default? (A blocking gate with no human is exactly
  the stall the CONTEXT.md "Interactive gate" note warns about for non-Maestro
  roles — but here the gate is *meant* to reach the human.)

## Non-goals

- Phase confirmations for **Leads or Workers** (they escalate to a parent, not
  the human — a blocking gate there would stall; see CONTEXT.md "Interactive
  gate").
- Mid-phase, free-form steering of a running Maestro (separate concern; this is
  a discrete boundary gate, not continuous control).
- Re-implementing the interactive-gate bridge — 003 owns that; 019 consumes it.

## Cross-cutting

Likely touches the Maestro role JD (`personalities/role-maestro.md`), the
process loop that drives a Maestro Turn, and possibly the Telegram bridge from
003. Declare reference-doc impact in `plan.md`.

## Sprint

Candidate for **S6** — S5 is already a heavy slice (its own risk note flags the
overrun), and 019 is a new feature orthogonal to S5's Workflow-engine goal.
Tracked as `planned` until a sprint commits it.
