# 028 — Scheduler facts-poke corrupts an entity parked at an interactive gate

## What

`PriorityScheduler` sends a "facts" prompt to **every** alive maestro on
its interval (`scheduler.py:221-222`) with **no guard** for an entity that
is parked at an interactive gate (AskUserQuestion / ExitPlanMode). The
poke is injected into the entity's live PTY stdin — and because the entity
is sitting at the gate's TUI menu, the injected text **submits the
highlighted default option**. The entity "answers" its own gate with a
choice the human never made.

## Why — 2026-06-13 live smoke (Run 1)

otter was parked at an un-bridged AskUserQuestion gate (see Ticket 029),
waiting on a human who never saw it.

- The gate offered "Workflow fan-out (Recommended)" (option 0) vs "Lead
  solo".
- The scheduler poked otter: `send_to_entity` injects the facts prompt
  into the PTY, then logs *on return* — `scheduler: poked otter` at
  **02:04:13**; the injection itself landed ~**02:04:04**, which is
  exactly when the gate's `tool_result` recorded the answer **"Workflow
  fan-out (Recommended)"** — option 0, the highlighted default.
- otter then "locked" that contract and proceeded. A maestro made a
  binding decision **the user never approved**. Had the option order
  differed, it would have locked the *wrong* choice.

The `gate_coordinator` is correctly designed to park forever and never
auto-decide (`gate_coordinator.py:11-12`, issue #25) — this bug
**bypasses** it entirely by writing to the raw TUI beneath it.

## Acceptance

- The scheduler (and any non-gate sender) **skips or defers** an entity
  with a pending interactive gate — detected via
  `gate_coordinator.pending_request_id(entity)` and/or the session-state
  `waitingFor` field — rather than injecting into its PTY.
- No code path can submit a gate's default option as a side-effect of an
  unrelated prompt injection.
- Test: a facts-poke dispatched at an entity with a pending gate does not
  resolve the gate and does not reach the PTY.
- `ruff` + `pytest -m "not integration"` green.

## Non-goals

- Bridging the gate to the user — **Ticket 029**. Note: until 029 lands,
  this guard is what stops a parked gate from being silently
  auto-answered, so it has standalone value.
- The no-progress timeout — **Ticket 027**.

## Notes

Found in the 2026-06-13 live smoke of Ticket 016. The most dangerous of
the Run-1 cluster: a silent, unauthorised decision. The mitigation (a
pending-gate check before poking) is cheap and worth landing even ahead of
029. S6 candidate.
