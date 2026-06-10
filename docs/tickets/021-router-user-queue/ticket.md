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

## Acceptance

- A maestro `message` action to `user` reaches Telegram via the
  router path, including when no user command is in flight (or the
  design documents why the fallback is sufficient and the warning is
  removed/downgraded with a comment).
- No `No queue for recipient user` warning in the happy path.
- Test covering a proactive (not command-response) maestro→user
  message.
- `ruff` + `pytest -m "not integration"` green.

## Non-goals

- Changing the `hive_actions` protocol or maestro JD wording.
- Dashboard chat persistence semantics (only touched if the routing
  fix lands there naturally).

## Notes

Trivial/small ticket — likely `ticket.md` → `plan.md`. Found during
the 2026-06-10 otter incident (see Ticket 020).
