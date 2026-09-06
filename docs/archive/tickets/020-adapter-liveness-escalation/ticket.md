# 020 — Adapter liveness escalation: auto-bounce jammed PTY sessions

## What

When an Entity's PTY session stops making progress — consecutive turn
`TimeoutError`s from the transcript reader — Hive should **bounce the
adapter automatically**: kill the jammed `claude` process, let
`_get_or_create_adapter` respawn it (`--continue` preserves the
conversation), notify the user, and retry or surface cleanly. Today a
jammed session stays jammed forever; every send burns the full
no-progress window (180s) and fails, until a human kills the PID.

## Why — incident 2026-06-10 (corrected after fuller diagnosis)

Live tests of maestro `otter` failed with
`No completed assistant turn … within 180.0s`. The **corrected** root
cause (an earlier draft of this ticket guessed OAuth expiry — that was
wrong; the trigger is detailed in Ticket 022):

- The jam is an **un-bridged interactive permission prompt**. CC's own
  session-state file is authoritative:
  `~/.claude/sessions/<pid>.json` showed
  `"status": "waiting", "waitingFor": "permission prompt"` for the
  stuck session.
- It is **not** OAuth, **not** version drift (jammed identically on
  2.1.170 *and* 2.1.172), and **not** caused by Ticket 015. The
  trigger is the maestro doing interactive work itself — see
  Ticket 022.
- Permission prompts are not transcript-detectable (ADR 0005), so the
  reader sees genuine no-progress and times out at 180s — **correct**
  behaviour. What's missing is escalation: a jammed PTY stays jammed
  until a human kills the PID. The manual fix was `kill <pid>` → Hive
  auto-respawned via `--continue` → next prompt answered in ~8s.

An auto-bounce converts this whole class — any transcript-invisible
prompt or wedged TUI state — from "jammed until a human notices" into
"self-heals in minutes." It is the **generic recovery net**; Ticket 022
prevents this specific trigger.

### New detection signal (supersedes ADR 0005's "not detectable")

ADR 0005 concluded permission prompts can't be detected from the
**transcript**. They now surface in a different channel: CC writes
`~/.claude/sessions/<pid>.json` with live `status` /`waitingFor`
fields (`"waitingFor": "permission prompt"`). This makes the
auto-bounce trigger precise — bounce when `waitingFor` is set, rather
than guessing from N blind timeouts — and is worth confirming as a
stable interface during research.

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
no-progress semantics (issue #78) are the timeout layer; the
`sessions/<pid>.json` `waitingFor` field (above) is the sharper
trigger. Paired with Ticket 022 (which prevents the specific trigger):
022 stops the maestro from hitting the prompt; 020 recovers any
session that hits one anyway. Both are S6 candidates.
