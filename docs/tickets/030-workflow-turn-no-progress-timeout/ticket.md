# 030 — Workflow-turn no-progress timeout false-fires on long runs

> Discovered during: the 016/018 live smoke (2026-06-13). A lead's Workflow
> turn completed successfully (3/3 leaf agents, ~16s of leaf work) yet the
> reader logged `Turn did not complete within 180.0s` and treated the turn as
> timed-out.

## What

Stop the reader's no-progress deadline from false-firing on a Lead's **Workflow
run** turn that is actually making progress (or has completed). Today a long
Workflow turn trips the 180s no-progress timeout — the friendly message (017
/ #118) fires, but the Turn is still treated as not-accepted, so the result and
any trailing `hive_actions` (the lead's report) are at risk and the parent sees
a timeout instead of a completion.

## Why

This is the reliability tax on the very path S5 built. 017 added a
liveness-reset that was meant to keep a Turn alive while its Workflow emits
progress, plus a friendly timeout message. The live smoke shows the **message**
works but the **acceptance** does not — long Workflow turns still get marked
timed-out. Until this holds, every non-trivial Lead fan-out looks like a
failure to the maestro, undermining 016/018's whole point.

## Acceptance

- A Lead Workflow turn that completes within the run's natural duration is
  **accepted on the turn-end sentinel**, never reported as a no-progress
  timeout — verified on deployed code with a multi-minute Workflow run.
- The liveness-reset re-arms on observed Workflow progress (run record updates
  / partial results), so the 180s deadline only fires on a genuinely silent
  Turn.
- The friendly timeout message (017 / #118) is preserved for the genuine-stall
  case; it does not fire on a healthy long run.
- A genuinely stalled Workflow turn still times out (no infinite hang) — pairs
  with Ticket 020 (auto-bounce).

## Non-goals

- Auto-bouncing / restarting a jammed session — **Ticket 020**.
- Steering a running Workflow — S7.

## Notes

Investigate first **why** 017's liveness-reset isn't holding: did it never arm
on Workflow progress, or does it fire too coarsely? Likely lives in the reader
/ `pty_session` turn-acceptance path. Part of the 027/029 turn-layer family.
