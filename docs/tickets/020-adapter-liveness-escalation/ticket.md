# 020 — Adapter liveness escalation: auto-bounce jammed PTY sessions

## What

When an Entity's PTY session stops making progress — consecutive turn
`TimeoutError`s from the transcript reader — Hive should **bounce the
adapter automatically**: kill the jammed `claude` process, let
`_get_or_create_adapter` respawn it (`--continue` preserves the
conversation), notify the user, and retry or surface cleanly. Today a
jammed session stays jammed forever; every send burns the full
no-progress window (180s) and fails, until a human kills the PID.

## Why — incident 2026-06-10 22:43 UTC

The first live test after the 015 deploy failed with
`No completed assistant turn … within 180.0s`. Diagnosis (session
`77fab09d…`, maestro `otter`):

- otter's turns worked 22:39–22:42, then the 22:43:31 prompt was
  accepted (user entry written) and the model **never made an API
  call** — zero sockets, zero CPU, event loop idle.
- Every later injection was swallowed **before reaching the
  transcript** — the session's input was jammed, not slow.
- Timing: the shared OAuth token (`~/.claude/.credentials.json`,
  8-hour lifetime) expired at ≈22:43 — the exact failure minute. Best
  explanation: the in-session refresh lost a rotation race to a
  sibling CC process (the VPS runs many long-lived sessions on one
  credentials file) and CC parked on a **login modal** — invisible to
  the transcript, same family as ADR 0005's permission gates.
- Fix was surgical: `kill <pid>` → next send auto-respawned with
  fresh credentials → same probe answered in 8s.

The reader's timeout behaved correctly (it *is* genuine no-progress);
what's missing is the escalation. An auto-bounce converts this entire
class — any transcript-invisible modal or wedged TUI state — from
"jammed until a human notices" into "self-heals in minutes."

## Acceptance

- Consecutive turn timeouts on one adapter (threshold configurable,
  default 2) trigger: kill the PTY, drop the cached adapter, respawn
  (`--continue`), notify (`Auto-bounced <entity> after N stalled
  turns`), and audit the event.
- A successful turn resets the counter.
- Conversation continuity across the bounce (the respawn picks up the
  prior session — same mechanism `_get_or_create_adapter` uses today).
- Hermetic tests at the adapter seam: simulated consecutive
  `TimeoutError`s produce exactly one bounce + notification; a success
  between timeouts produces none.
- A bounce loop guard: if the respawned session times out again
  immediately (M bounces in a window), stop bouncing and surface an
  error instead of flapping.
- `ruff` + `pytest -m "not integration"` green.

## Non-goals

- Detecting the modal itself (ADR 0005: not transcript-detectable).
- Per-Entity credential isolation (eliminates the rotation race at the
  source — bigger change, Phase 5 adapter-layer territory; note it in
  research as the root-cause fix this ticket only mitigates).
- The read-only progress watcher (Ticket 017) — complementary: the
  watcher makes a jam *visible*, this ticket makes it *self-healing*.

## Notes

Found while diagnosing the failed 015 live smoke test. The reader's
no-progress semantics (issue #78) are the detection layer this ticket
builds escalation on.
