# 021 — Route maestro→user messages first-class (no dead-letter warning)

## What

A maestro's `hive_actions` `message` to `user` currently logs

```
[hive.bus.router] WARNING: No queue for recipient user, message logged but not delivered
```

because the router has no `user` queue — the reply only reaches
Telegram through the command dispatcher's direct-response fallback.
Make `user` a first-class routable recipient (a sink that forwards to
the notification/bridge layer), or explicitly handle the path and
drop the misleading warning.

## Why

- The maestro JD instructs "report to the user proactively … send a
  `<hive_actions>` message to `user`" — the *documented* reporting
  path produces a dead-letter warning on every use.
- Today delivery works only when the message rides the same turn that
  a user command is awaiting (the fallback). A proactive mid-task
  report (e.g. "all leads done" arriving while no command is pending)
  may genuinely vanish — **confirm in research**; observed on
  2026-06-10 22:42 during the otter incident.
- Noisy WARNINGs train operators to ignore the log level.

## Live repro — 2026-06-13 (confirmed)

The Ticket 016 live smoke confirmed the "may genuinely vanish" path. A
clean maestro→lead→Workflow run finished successfully (lead
`otter.strutils`, 18/18 tests green, repo untouched) and the lead reported
DONE up to otter. otter then emitted its consolidated results `message` to
`user` — with **no user command in flight** — and it was dropped:

```
03:21:54 [hive.process.message_dispatcher] WARNING: Unknown recipient: user
```

The results never reached Telegram. Note the log line differs from the one
quoted above (`[hive.bus.router] No queue for recipient user`): the
dead-letter now surfaces from **`message_dispatcher`** as `Unknown
recipient: user`. Confirm in research whether the path moved or there are
two dead-letter points.

### Folded in: maestro narrates fictional delivery

After the drop, otter's own transcript said *"the consolidated report has
been delivered"* — it believed it had succeeded. The dropped `message`
action returns no failure signal to the model, so the maestro violates its
own JD rule ("if a hive_action fails, report the failure honestly; do not
narrate fictional success"). Routing alone is not enough — a dropped or
failed `message` must be surfaced back to the sender, or the failure stays
hidden even after the queue exists.

## Acceptance

- A maestro `message` action to `user` reaches Telegram via the
  router path, including when no user command is in flight (or the
  design documents why the fallback is sufficient and the warning is
  removed/downgraded with a comment).
- No `No queue for recipient user` warning in the happy path.
- Test covering a proactive (not command-response) maestro→user
  message.
- A `message` action that cannot be delivered returns a failure to the
  maestro (so it does not narrate fictional delivery) — covered by a test.
- `ruff` + `pytest -m "not integration"` green.

## Non-goals

- Changing the `hive_actions` protocol or maestro JD wording.
- Dashboard chat persistence semantics (only touched if the routing
  fix lands there naturally).

## Notes

Small ticket — likely `ticket.md` → `plan.md`, now scoped a touch larger
by the folded-in false-delivery fix. Found during the 2026-06-10 otter
incident (see Ticket 020); **confirmed live 2026-06-13** in the Ticket 016
smoke (the residual bug a clean maestro→lead→Workflow run still hits — the
only broken leg once Tickets 027/028/029 remove the gate cluster).
