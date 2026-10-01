# 0015 — Auto-bounce jammed sessions, guarded by liveness checks

**Status:** Accepted (2026-06-14)
**Ticket:** [020](../tickets/020-adapter-liveness-escalation/)

## Context

A PTY Entity can jam: the Harness stops producing turn output and every
prompt burns the full 180s no-progress window and fails. The observed
trigger (otter, 2026-06-10) was an **un-bridgeable permission prompt** —
CC's session-state file read `{"status":"waiting","waitingFor":"permission
prompt"}` while nothing reached the transcript. ADR 0005 established that
permission prompts have **no transcript signature**, so the reader cannot
detect the gate and correctly times out — but nothing recovers. The session
stays jammed until a human runs `kill <pid>`; the manual fix then
auto-respawned via `--continue` and the next prompt answered in ~8s.

The reader's no-progress timeout is also the same one Ticket 030 says
**false-fires** on healthy long Workflow turns, and a maestro legitimately
parked at a plan/ask gate (Ticket 028/029) looks identical to "no progress"
from the outside. So a naive "N timeouts → kill" would friendly-fire two
legitimate waits.

## Decision

Hive **auto-bounces** a jammed session — kill the Harness process, evict
the cached Adapter, respawn (conversation preserved automatically via
`--continue`), notify, and audit — **guarded by two liveness safety
checks** re-run at decision time:

1. **`is_parked_at_gate`** — skip the bounce while the Entity waits at a
   bridged plan/ask gate. This cleanly excludes legitimate waits *and*
   isolates the target: a permission prompt is undetectable as a gate
   (ADR 0005), so it never registers, so the check is False for exactly the
   jam we want to kill. The *absence* of a registered gate is the signal.
2. **`workflow_active`** — skip the bounce while a Lead's Workflow run is
   still advancing (the same predicate the reader uses to reset its own
   deadline). This makes 020 robust to Ticket 030's false-timeout whether
   or not 030 has landed.

Bounce only when a stall threshold (default 2 genuine stalls) is reached
**and both checks are clear**. A **time-windowed flap-guard** (default 3
bounces / 30 min) stops an endless kill→respawn loop: it moves the Entity
to `ERROR` and escalates to the user instead of flapping.

The bounce notification carries a **best-effort reason**, assembled at
bounce time from the session-state file's `status`/`waitingFor`, the
process liveness, and the last transcript entry, falling back to "cause
unknown". The `waitingFor` field is used **advisory-only** — for the
*message*, never the *decision*.

## Alternatives rejected

- **Blind consecutive-timeout count.** Kills a maestro waiting on the user
  and a Lead mid-Workflow. The two safety checks are cheap, already-present
  reads that prevent both.
- **`waitingFor` as the bounce trigger** (the sharper signal). It is an
  undocumented CC interface; making *recovery* depend on it is fragile, and
  it only covers the permission class, not wedged-TUI / hung-process jams.
  Kept as advisory diagnosis instead — value without fragility.
- **Detecting and bridging the permission prompt** (closing ADR 0005's
  gap). No transcript signature exists; recovery is orthogonal and far
  cheaper than a screen-state detector.
- **Flap-guard as an absolute bounce cap.** Permanently disables
  self-healing for a long-lived Entity; a time window refreshes the budget.
- **Counter stored on the Adapter.** Wiped by the very bounce it must
  survive; state lives on the ProcessManager.

## Consequences

- A transcript-invisible jam (permission prompt, wedged TUI, hung process)
  self-heals in minutes instead of stranding until a human notices — the
  generic recovery net for the whole class.
- 020's correctness leans on the **public** `is_parked_at_gate` contract
  (Ticket 028) and the `workflow_active` predicate (Ticket 017), not on
  their internals — so 029 (gate-bridge rework) and 030 (liveness-reset)
  can churn freely. A regression test pins "a gated maestro is never
  bounced".
- The reason path consumes an **undocumented** CC field (`waitingFor`),
  guarded only by the version pin (Ticket 009). Because it is advisory, a
  format change degrades the *message* ("cause unknown"), never the
  recovery. Re-verify the `waitingFor` vocabulary when bumping CC.
- This does **not** fix the root cause (the permission prompt occurring at
  all) — Ticket 022 prevents the specific trigger; per-Entity credential
  isolation (Phase 5) is the deeper fix. 020 mitigates any jam.
- A genuinely broken session ends in `ERROR` with a reasoned escalation,
  not an infinite kill loop. Recovery from `ERROR` remains a human action.
