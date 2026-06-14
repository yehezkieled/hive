# 0018 — Maestros ask the user via a conversational decision channel, not the native-gate bridge

**Status:** Accepted (2026-06-14)
**Ticket:** [029](../tickets/029-maestro-gate-bridge-regression/)
**Relates to:** [ADR 0004](0004-interactive-gate-hold-and-inject.md) (the bridge
this retires for maestros), [ADR 0008](0008-per-role-skill-curation-denylist.md)
/ [ADR 0010](0010-leads-orchestrate-via-workflow.md) (the per-role tool denylist
this extends), Tickets [019](../tickets/019-maestro-phase-confirmation/),
[021](../tickets/021-router-user-queue/), [031](../tickets/031-maestro-lead-addressing/)

## Context

Ticket 003 (ADR 0004) bridged a maestro's mid-Turn native interactive gate
(`ExitPlanMode` / `AskUserQuestion`) to the user on Telegram by holding the PTY
turn open, translating the TUI menu to text, and injecting keystrokes back
("hold-and-inject"). Ticket 029 found that path broken for a maestro
`AskUserQuestion` and, on investigation, structurally fragile:

- A maestro is a **conversational** entity (addressed from Telegram). A native
  gate is a **TUI menu** with no Telegram representation, so the bridge must
  detect the gate in the transcript, translate the menu, hold the turn open, and
  inject arrow-keys + Enter. Every fragile part of 003/029 lives in that
  translation — it re-implements a conversation loop *inside one frozen turn*.
- The first-pass 029 fix (reorder the reader so the gate check is authoritative
  over sentinel acceptance) hardens that machinery — but only matters if we keep
  it. It was an early draft, abandoned in favour of this redirect (never built,
  no standalone ADR).
- Ground truth from `tool_policy.py` (ADR 0010): `ExitPlanMode` is **already**
  bare-name-denied to maestros (and has been since Ticket 015 with no plan-gate
  freezes — evidence bare-name tool denial works). Only `AskUserQuestion`
  remains. So the bridge survives, for a maestro, to service exactly one tool —
  when the codebase already proves we can just deny it.

Meanwhile Hive already has the loop the bridge re-implements: an entity speaks,
the turn ends, the user replies, the next turn reads it. And it already has the
verb: `request_decision` (today lead→maestro only).

## Decision

**Maestros ask the user through the conversational decision channel, and native
interactive gates are retired for maestros.**

1. **Ask via `hive_action`.** A maestro emits `request_decision{ to:"user",
   text:… }`, which routes to Telegram and **ends the turn** (remaining actions
   in the block are truncated — "ask then act in the same turn" is impossible).
2. **Park on a durable flag.** Emitting it sets `awaiting_decision` on the
   entity (a persisted column, restored on boot). The scheduler **skips** any
   entity with the flag set — so nothing but the user advances it, across
   restarts. A reused ~hourly nudge re-pings the user; it never auto-decides.
3. **Clear on a user-sourced reply.** Any inbound message **from the user**
   clears the flag and wakes the maestro; a peer-entity message does not. Hive
   does not parse intent — the maestro reads the reply and decides (proceed /
   revise+re-ask / answer+re-ask). Money is excluded: the vault
   `request_payment` rail keeps its own hard approve/deny path.
4. **Deny the native gates.** Add `AskUserQuestion` to `_MAESTRO_DENY`
   (`ExitPlanMode` already there). With zero native gates emittable, the 003
   bridge is vestigial for maestros and retired — at most a cheap
   detect-and-dismiss guard remains (inject Esc + nudge), kept only if a binary-
   confirm shows bare-name denial leaks. The detector (`gates.py`) survives; the
   translate/hold/inject path does not.

Delivery to `user` is the **one** sink that `message{to:user}` (Ticket 021) and
`request_decision{to:user}` share; the `self.<team>` alias resolver (Ticket 031)
is shared by both branches. Ticket 019's phase-confirmation gate is this channel
fired at a phase boundary — not a native gate.

## Alternatives rejected

- **Harden the bridge (the first-pass 029 reader-reorder).** Correct only if we
  keep the bridge. Abandoned.
- **Keep a native gate just for 019.** Forces `AskUserQuestion` to stay allowed
  and the bridge to stay live — re-accepting the fragility. Rejected in favour
  of re-mechanizing 019 onto this channel.
- **Hive parses the reply for approve/deny.** Re-creates the rigid structured
  gate; discards any reply that isn't a keyword. Rejected — keep Hive dumb about
  content, the maestro smart.
- **Wall-clock timeout on the wait.** Contradicts park-forever / never-auto-
  decide. The nudge covers AFK.

## Consequences

- The maestro→user interaction no longer depends on TUI-menu translation or
  keystroke injection — it rides the message loop Hive already runs. The
  "guarantee" the maestro can't run off lives in a persisted flag the scheduler
  respects, not in a frozen PTY.
- The 003 interactive-gate bridge becomes vestigial for maestros (and leads
  never used it). The gate subsystem shrinks to, at most, a detect-and-dismiss
  guard.
- New CC-behaviour dependency to verify on the pinned binary: `--disallowedTools
  AskUserQuestion` blocks emission (the `ExitPlanMode` precedent says yes).
- Cross-ticket: 021 becomes a prerequisite (the shared `user` sink), 031 a
  shared dependency (the alias resolver), and 019 is re-mechanized onto this.
- `awaiting_decision` adds a persisted entity field + migration; restart
  re-adoption of the waiting state aligns with Ticket 025's crash-recovery
  theme.
