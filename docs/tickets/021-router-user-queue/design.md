# Design — Ticket 021: route maestro→user messages first-class

Make a maestro's `message` action to `user` deliver to Telegram via the
existing notification sink, gated to maestros, fire-and-forget — and reject
(don't fake) when there's no path. Mirrors 029's `request_decision`→user
(ADR 0018) in structure; diverges on turn behaviour.

## §1 — Architecture: dispatcher special-case (chosen)

Handle `recipient_name == "user"` as a branch inside the `message` handler,
**after** alias resolution and **before** the Entity lookup (since `user` is
not an Entity). Delivery is `_mgr._notify(...)`, not `router.route(...)`.

```
 message handler (message_dispatcher.py)
   requested_to   = action.to
   recipient_name = _resolve_message_alias(entity, requested_to)
   ┌─────────────────────────────────────────────┐
   │ if recipient_name == "user":   ← NEW (021)   │
   │     gate → notify-or-reject → audit → continue│
   └─────────────────────────────────────────────┘
   recipient = _entities.get(recipient_name)     ← unchanged entity path
   ...
```

### Alternative considered — `user` as a router recipient (rejected)

Register a `user` queue in `bus/router.py` that auto-forwards to `_notify`, so
`message`/`request_decision`/everything routes uniformly through the bus.

Rejected because: the router is **entity↔entity mail** — each recipient has a
turn loop that drains its queue. `user` has no turn loop, so the queue would
need magic auto-forwarding, leaking notification semantics into the bus. And
029 already delivers to `user` via a dispatcher special-case, *not* the router —
so Option B would make 029 the inconsistent path. Option A keeps both
maestro→user channels symmetric and is the right size for a direct-lane ticket.
Not ADR-worthy (small, reversible, mirrors precedent).

## §2 — Permission gate: maestros only

New `can_message_user(sender_role: str) -> bool` in `bus/permissions.py`, beside
`can_request_decision`:

```python
def can_message_user(sender_role: str) -> bool:
    """Only a maestro may message the user directly. A lead reports through
    its maestro (mirrors can_request_decision's user rule)."""
    return sender_role == "maestro"
```

Role-only — the rule needs no name or target. Every maestro qualifies (incl.
the PA maestro). A lead's `message to:"user"` is rejected via `_reject_action`
with a hint to use `to:"maestro"`.

## §3 — Turn behaviour: fire-and-forget `continue` (diverges from 029)

| | `request_decision`→user (029) | `message`→user (021) |
|---|---|---|
| transport | `_notify` | `_notify` (same) |
| parking | `awaiting_decision = True` | none |
| nudge clock | `last_nudged_at = now` | none |
| persist | yes | no |
| loop control | **`break`** (end turn) | **`continue`** (keep processing) |

A report is informational — nothing to wait on, so no parking and no
"ask-then-act" hazard to guard. `break` exists in 029 only to stop a maestro
acting on a decision before the human answers; a report has no pending answer.
So a maestro can emit `message to:"user"` *and* `finish_task` in one block.

## §4 — The branch (reference shape)

```python
if recipient_name == "user":
    # Ticket 021: maestro→user one-way report. Mirrors 029's
    # request_decision→user (ADR 0018) but fire-and-forget — no park, no break.
    body = action.text or ""
    if not can_message_user(entity.role):
        await self._reject_action(
            entity, "message", requested_to,
            'only a maestro may message the user; report to your maestro '
            'via to:"maestro" instead.',
        )
        continue
    if self._mgr.notification_dispatcher is None:
        await self._reject_action(
            entity, "message", "user",
            "no notification path to the user is configured — your message "
            "was not delivered.",
        )
        continue
    await self._mgr._notify(
        f"[{entity_name}] {body}",
        kind="entity_message",
        data={"entity": entity_name},
    )
    self._mgr._last_routed_actions.append("user")
    await self._mgr._audit(
        "user_message_sent",
        target="user",
        details={"sender": entity_name, "text": body[:200]},
        actor=entity_name,
    )
    continue
```

Check order mirrors 029: permission → notification-path → deliver. No
empty-body check (mirrors 029's `text = action.text or ""`).

## §5 — Side effects: none

- **No ADR** — extends ADR 0018; the A/B choice is reversible and unsurprising.
  The code comment cites Ticket 021 / 029.
- **No CONTEXT.md term** — 029 didn't gloss its channel; glossing the smaller
  report sibling alone would be lopsided. A future pass can gloss both
  maestro→user channels together.
- **No maestro JD change** — JD already documents `message`→user (R7); a non-goal.

## §6 — Acceptance mapping (from `ticket.md`)

| Acceptance criterion | Met by |
|---|---|
| maestro `message`→user reaches Telegram, incl. no command in flight | §1/§4 `_notify` (proactive sink, command-independent) |
| no `No queue for recipient user` warning in happy path | R2 — that site was already unreachable for `user`; happy path now hits `_notify` |
| test: proactive (non-command-response) maestro→user message | outline tests T2/T5 |
| undeliverable `message` returns a failure (no fictional delivery) | §4 no-path branch → `_reject_action`; R3 |
| `ruff` + `pytest -m "not integration"` green | plan Verification |
