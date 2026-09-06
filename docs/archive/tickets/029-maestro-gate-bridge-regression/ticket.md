# 029 — Maestro interactive gate (AskUserQuestion) not bridged to the user

## What

A maestro's `AskUserQuestion` gate did **not** reach the user on Telegram.
The `gate_coordinator` never created a gate row (zero coordinator activity
in the logs), so the `Gated` path in `pty_session`
(`pty_session.py:319-323`) never engaged. The turn sat at the gate
unbridged until the no-progress deadline fired (Ticket 027) and a stray
scheduler poke phantom-answered it (Ticket 028). Ticket 003 (the
interactive-gate bridge) is **done**, so this is a regression or an
uncovered case.

## Why — 2026-06-13 live smoke (Run 1)

- otter received a prompt that asked it to "propose, then wait for
  approval." It emitted a proposal (assistant text) followed by an
  `AskUserQuestion` — a recognised gate (`gates.py:92`).
- The journal shows **no** gate / coordinator / approval / question
  activity for the entire turn. The user's Telegram showed only
  `prompt → error`, never the question.
- The reader timed out at 180s (02:00:59) and shipped the raw error; the
  gate was then phantom-answered by a scheduler poke at 02:04:04
  (Ticket 028) — not by the user.
- **Run 2 avoided it** by dropping the "wait for approval" step (no gate
  emitted), which is why the second run looked clean. So the trigger is
  the maestro emitting an AskUserQuestion (the propose-and-wait pattern)
  and the bridge not firing for it.

## Acceptance

- A maestro `AskUserQuestion` (and `ExitPlanMode`) gate creates a gate row
  and surfaces to the user on Telegram, per Ticket 003 — including when
  the gate follows assistant text in the same turn.
- While the user decides, the turn holds open (the `Gated` re-await,
  `pty_session.py:313-318`), so the no-progress deadline does not fire and
  no raw error is shipped.
- Root-caused: *why* the detector/coordinator did not engage on otter's
  session — gate_detector wiring on maestro PTYs (`pty_session.py:223`,
  gated on `gate_coordinator is not None`), or a gate emitted after
  assistant text not being detected.
- Test reproducing the Run 1 scenario: proposal text + AskUserQuestion on
  a maestro bridges to the notification channel and parks (does not time
  out).
- `ruff` + `pytest -m "not integration"` green.

## Non-goals

- The scheduler poke that exploited the un-bridged window — **Ticket
  028**.
- The no-progress timeout firing during the wait — **Ticket 027** (a
  bridged gate should hold the turn open, so the timeout never applies).

## Notes

Found in the 2026-06-13 live smoke of Ticket 016. Pairs with Ticket 003
(the bridge that should have fired) and Tickets 020 / 022 (jam recovery /
prevention for the maestro-hits-a-prompt class). The three Run-1 cluster
tickets — 027 (timeout), 028 (poke), 029 (bridge) — chain off each other;
029 is the upstream cause. S6 candidate.
