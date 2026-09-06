# Plan — Ticket 021: route maestro→user messages first-class  (issue [#171](https://github.com/yehezkieled/hive/issues/171))

Direct lane — one PR that closes [#171](https://github.com/yehezkieled/hive/issues/171).
Make a maestro's `message` action to `user` deliver to Telegram via the existing
`_notify` sink (029's path), gated to maestros, fire-and-forget — and reject
(not fake) when there's no path. Full spec in #171; approach in `design.md`.

## Files this Ticket creates / modifies

| Path | Op | Step |
|------|----|------|
| `src/hive/bus/permissions.py` | edit | add `can_message_user(sender_role: str) -> bool` (`== "maestro"`) beside `can_request_decision`, with a docstring noting it mirrors that user rule |
| `src/hive/process/message_dispatcher.py` | edit | import `can_message_user`; in the `message` branch, **after** `_resolve_message_alias` and **before** the `_entities.get` lookup, add the `recipient_name == "user"` branch: gate → no-notification-path reject → `_notify(f"[{name}] {body}", kind="entity_message", data={"entity": name})` → audit `user_message_sent` → `_last_routed_actions.append("user")` → `continue` (`design.md` §4) |
| `tests/process/test_message_dispatcher.py` | add | tests T1–T6 from `outline.md`, mirroring 029's `request_decision`→user test setup |

## Verification
- `ruff check src/ tests/ && ruff format --check src/ tests/ && pytest -m "not integration"` green.
- **T1** unit `can_message_user`: `"maestro"`→True, `"lead"`→False.
- **T2** maestro `message`→user, dispatcher present → `_notify` called once with `text="[<name>] <body>"`, `kind="entity_message"`; `user_message_sent` audit; `_last_routed_actions == ["user"]`.
- **T3** dispatcher `None` → `_reject_action` ("no notification path"), **no** `_notify`.
- **T4** lead `message`→user → rejected by gate, **no** `_notify`, hint names `to:"maestro"`.
- **T5** maestro block: `message`→user **then** a trailing action → both run (proves `continue`, not `break` — the divergence from 029).
- **T6** regression: maestro→lead peer `message` still routes via `router.route`.
- **Deployed re-smoke (S6 rule):** a maestro proactively reports to `user` with no command in flight and it lands on Telegram. Holds #171 open until this passes.

## Out of scope
- `hive_actions` protocol / maestro JD wording changes (non-goal).
- `user` as a router-queue recipient — rejected in `design.md` §1 (dispatcher special-case mirrors 029).
- The `request_decision`→user path (029, already shipped).

## Cross-cutting impact
- **None to reference docs.** No ADR (extends [ADR 0018](../../adr/0018-conversational-decision-channel.md); the A/B choice is reversible and unsurprising — a code comment cites Ticket 021/029). No CONTEXT.md term (029 didn't gloss its channel; a future pass can gloss both maestro→user channels together). No README/DEPLOYMENT (no new service/port). Ships in `src/` + tests only.

## Build
One branch (`ticket-021/user-routing`), one PR that closes #171. Build directly
(you or a single agent) — not a fleet ticket.
