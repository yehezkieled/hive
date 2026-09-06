# Research — Ticket 021: route maestro→user messages first-class

All claims below carry a file reference. Verified against the code on branch
`ticket-021/user-routing` (forked from `main` @ 04a91bf).

## R1 — Where a `message`→`user` dies today

`MessageDispatcher._handle_actions`, the `message` branch
(`src/hive/process/message_dispatcher.py:310-323`):

```
recipient_name = _resolve_message_alias(entity, "user")   # → "user" (no alias)
recipient      = _entities.get("user")                     # → None (user ≠ entity)
if not recipient:
    logger.warning("Unknown recipient: %s", requested_to)  # :315
    await _reject_action(entity, "message", "user", "unknown recipient ...")
    continue
```

`user` is **not** an Entity, so the lookup at `:313` returns `None` and the
action is rejected at `:315`. It never reaches the router or the notification
layer.

## R2 — Two dead-letter points, non-overlapping for `user`

| Site | Fires when | Hit by `user`? |
|------|-----------|----------------|
| `message_dispatcher.py:315` "Unknown recipient" | recipient name resolves to no known Entity | **yes** — `user` isn't an entity |
| `bus/router.py:91` "No queue for recipient … logged but not delivered" | `router.route()` called for an Entity with no queue | no — `user` is rejected before `route()` |

The ticket saw the log line "move" between two runs because the
entity-existence check now happens in the dispatcher (`:313`) *before*
`router.route()`. For `user`, `bus/router.py:91` is unreachable. → 021 only
needs to handle the dispatcher site.

## R3 — "Fictional delivery" is already structurally fixed

`_reject_action` (`message_dispatcher.py:705`, shipped in Ticket 023) does two
things: audits `action_rejected`, **and** routes a `system → sender` note —
`"[action rejected] your message to 'user' was not delivered: …"`
(`:731-735`). So a maestro messaging `user` today **already receives a failure
signal** and should not narrate fictional success. The ticket's folded-in
"narrates fictional delivery" observation (2026-06-13 smoke) predates this
wiring on the message path.

→ **Scope shrink:** 021 does *not* need a new failure mechanism. It reuses
`_reject_action` on the undeliverable branch (no notification path). The real
work is making the happy path actually *deliver*.

## R4 — The user-delivery sink already exists (029 precedent)

029's `request_decision`→`user` (`message_dispatcher.py:389-430`) delivers via:

```
await self._mgr._notify(text, kind="decision_request", data={"entity": ...})
```

- `_notify` (`manager.py:525-536`) forwards to `notification_dispatcher.dispatch(Notification(...))`.
- `NotificationDispatcher` (`notifications/dispatcher.py:39-70`) fans out to every
  registered channel (Telegram bridge, SSE, email) — failures isolated per channel.
- 029 guards with `if notification_dispatcher is None: _reject_action(...)`
  (`:396-411`) — *don't* claim delivery when there's no path.

→ 021 reuses this sink verbatim; no new transport.

## R5 — Permission shape

- `can_message(sender_role, sender_name, recipient_role, recipient_name)`
  (`bus/permissions.py:20`) compares two **Entity** role/name pairs — it cannot
  gate `user`, which has neither.
- `can_request_decision(sender_role, sender_name, target_name)`
  (`bus/permissions.py:89`) already encodes the rule we mirror: `lead` →
  only its own maestro; `maestro` → only `"user"`; else `False`. A lead never
  escalates to the user directly.
- No `can_message_user` exists today (grep clean) → 021 adds a minimal,
  role-only gate beside `can_request_decision`.

## R6 — `kind` is a tag, not a renderer

`Notification.kind` (`notifications/dispatcher.py:13-26`): *"kind exists for
future per-channel routing … for now every channel receives every event."*
`data` feeds the web UI's interactive bubbles. → What the user *sees* is the
`text` field; `kind` is a semantic label only. 021 uses `kind="entity_message"`
and name-prefixes the text.

## R7 — The maestro JD already documents (and depends on) this path

`personalities/role-maestro.md`:
- `:3,:17` — *"Propose a plan back to the user via a `message` action."*
- `:37-39` — *"Report to the user proactively … send a `<hive_actions>` message
  to `user`."*
- `:22,:106-111` — the *blocking* question uses `request_decision` to `user`
  (029), explicitly distinct from the one-way `message`.

→ The JD's phase-1 flow is **propose-via-`message` → confirm-via-`request_decision`**.
029 fixed the confirm half; the propose/report half (`message`→`user`) is
**dead today** (hits R1). 021 makes the already-documented instruction work. No
JD change needed — the message/decision distinction is already correct there.

## Confirmed scope

A single dispatcher branch + one permission function + tests. No router change,
no new transport, no reference-doc edits. Direct lane.
