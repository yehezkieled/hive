# Questions — Ticket 021: route maestro→user messages first-class

The unknowns going in. All resolved by the `grill-with-docs` session
(answers carried into `research.md` / `design.md`).

## Q1 — One dead-letter point or two?

The ticket quotes two different log lines (`[hive.bus.router] No queue for
recipient user` vs `[message_dispatcher] Unknown recipient: user`). Are there
two drop sites, and which one does `user` actually hit?

→ **Two sites, non-overlapping for `user`.** `message_dispatcher.py:315`
fires first because `user` isn't an entity; `bus/router.py:91` only fires for
an entity that exists but has no queue. `user` never reaches the router. See
`research.md` R2.

## Q2 — Does a proactive maestro→user message genuinely vanish?

The ticket flags "may genuinely vanish — confirm in research."

→ **Yes, the *delivery* vanishes** (never reaches Telegram), but since Ticket
023 the maestro now gets a *rejection note* back via `_reject_action`. So the
"narrates fictional success" half is already structurally fixed; the real gap
is delivery. See R1/R3.

## Q3 — Is there a reusable user-delivery sink, or do we build one?

→ **Reuse the existing one.** `_mgr._notify(...) → notification_dispatcher →
Telegram`, exactly what 029's `request_decision`→user uses. No new transport.
See R4.

## Q4 — Special-case the path, or make `user` a real router recipient?

→ **Special-case in the dispatcher** (Option A), mirroring 029. The bus router
is entity↔entity mail with per-entity drain loops; `user` has no turn loop, so a
router queue would need magic auto-forwarding and would leak notification
semantics into the bus — and make 029 the inconsistent one. See `design.md` §1.

## Q5 — Who may message `user`?

→ **Maestros only.** Mirrors `can_request_decision`: a lead never reaches the
user directly (it reports through its maestro). New `can_message_user(role)`.
See `design.md` §2.

## Q6 — Does messaging `user` end the turn (like `request_decision`)?

→ **No — fire-and-forget `continue`.** A report isn't a blocking question, so
no `awaiting_decision`, no nudge clock, no `break`. Diverges from 029 here. See
`design.md` §3.

## Q7 — Reference-doc / decision side effects?

→ **None.** No ADR (extends ADR 0018; the A/B choice isn't hard-to-reverse or
surprising). No CONTEXT term (029 didn't gloss its channel either). No maestro
JD change (the JD already documents `message`→user; it's a non-goal). See
`design.md` §5.
