# Outline — Ticket 021: route maestro→user messages first-class

Implementation structure for the single PR. Detail in `design.md`.

## Steps

1. **`bus/permissions.py`** — add `can_message_user(sender_role: str) -> bool`
   (returns `sender_role == "maestro"`), placed beside `can_request_decision`
   with a docstring noting it mirrors the user rule there.

2. **`process/message_dispatcher.py`** — import `can_message_user`; in the
   `message` branch, after `recipient_name = _resolve_message_alias(...)` and
   **before** the `_entities.get(...)` lookup, insert the `recipient_name ==
   "user"` branch (`design.md` §4): gate → no-path reject → `_notify` → audit
   `user_message_sent` → append `"user"` → `continue`.

3. **`tests/process/test_message_dispatcher.py`** — add the tests below,
   mirroring the existing `request_decision`→user test setup (fake/spy on
   `_notify` / `notification_dispatcher`).

## Tests

| # | Scenario | Asserts |
|---|----------|---------|
| T1 | unit `can_message_user` | `"maestro"`→True; `"lead"`→False |
| T2 | maestro `message to:"user"`, dispatcher present | `_notify` called once with `text="[<name>] <body>"`, `kind="entity_message"`; `user_message_sent` audit; `_last_routed_actions == ["user"]` |
| T3 | maestro `message to:"user"`, `notification_dispatcher is None` | **no** `_notify`; `_reject_action` ("no notification path"); not counted as routed |
| T4 | lead `message to:"user"` | rejected by gate; **no** `_notify`; reject hint names `to:"maestro"` |
| T5 | maestro block: `message to:"user"` **then** a second action | both process — proves `continue`, not `break` (the divergence from 029) |
| T6 | regression: maestro→lead peer `message` | still routes via `router.route` (entity path unchanged) |

## Verification gate

`ruff check src/ tests/ && ruff format --check src/ tests/ && pytest -m "not integration"`

Plus the S6 deployed re-smoke (see `plan.md`): a maestro proactively reports to
`user` and it lands on Telegram with no command in flight.
