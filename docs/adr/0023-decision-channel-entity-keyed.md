# ADR 0023 — The web decision channel stays entity-keyed and one-deep: question on the entity row, not a DecisionStore

- **Status:** Accepted
- **Date:** 2026-06-21
- **Ticket:** [038](../tickets/038-web-decision-ui-parity/) (web decision-UI parity)
- **Relates to:** [ADR 0018](0018-conversational-decision-channel.md) (the 029
  channel this surfaces on the web), [ADR 0019](0019-maestro-phase-confirmation-gate.md)
  (the `confirmed_with_user` side effect a reply triggers), [ADR 0022](0022-responsive-touch-shell-contract.md)
  (the 037 drawer/badge surface 038 renders inside)

## Context

Ticket 038 ports the 029 maestro→user decision channel (ADR 0018) to the web: a
maestro's `request_decision{to:"user"}` arrives over SSE but renders as a dead
plain line with no question text and no reply field, and it isn't recoverable
after a reload.

Hive's two existing user-approval flows — `mode_request` and `vault_action` — set
a strong precedent. Both are backed by a row-id'd Postgres store
(`ModeRequestStore`, `VaultStore`) with `create … RETURNING id`, a
`pending→approved/denied` lifecycle, and web endpoints keyed by that id
(`POST /api/mode-request/{id}/approve`, `GET /api/mode-requests/pending`). The
obvious move is to mirror it: a `DecisionStore` table with a row per decision and
`POST /api/decision/{id}/reply`.

But the decision channel is **structurally different** from an approval:

- It is **one-deep per maestro.** `awaiting_decision` is a single durable bool
  (ADR 0018), and emitting `request_decision` ends the turn (`break`), so a
  maestro physically cannot have two open questions at once. There is no
  multi-row, id-addressed queue to model.
- It is **free-text Q&A, not approve/deny.** A reply is prose the maestro
  interprets ("proceed / revise / answer+re-ask"); Hive stays dumb about content
  (ADR 0018). `requested_mode` / `chosen_option` / allow-deny columns model
  nothing here.
- The **resume path already exists and is shared.** A user reply unparks + resumes
  the maestro through `CommandDispatcher._send_to_entity`
  (`clear_awaiting_decision → send_to_entity → route`, `dispatch.py:645-648`) —
  the same path Telegram and `/api/command` use. `clear_awaiting_decision` also
  flips `confirmed_with_user` (the Ticket 019 phase floor) and must run **before**
  the send so a same-turn re-ask can re-arm.

The only thing genuinely missing for the web is **durability of the question
text** — today it lives only in the transient SSE `text` and the maestro's
transcript, so a reload or a dropped SSE frame loses it.

## Decision

**Surface the decision channel on the web by extending the entity, not by
introducing a parallel store. Key everything by entity; keep `awaiting_decision`
the single source of truth.**

1. **Question on the entity row.** Add a nullable `last_decision_question` column
   (migration 032), set in the `request_decision→user` handler right where
   `awaiting_decision=True` is set; `clear_awaiting_decision` nulls it.
2. **Entity-keyed reply endpoint.** `POST /api/decision/{entity}/reply` — `{entity}`
   is always a top-level maestro (only a maestro may `request_decision` to `user`).
   It is a **thin wrapper**: it builds `Command("message", entity, reply)` and
   delegates to the existing `dispatch_command` message path, so the
   clear→send→route sequence (and the phase-confirm side effect, and the
   clear-before-send ordering) is never forked.
3. **Pending by scan, not by query.** `GET /api/decisions/pending` derives the
   list from entities with `awaiting_decision=True` + their `last_decision_question`.
   It mirrors the *shape* of `/api/mode-requests/pending`, not its backing table.
4. **Enrich the payload, not the schema.** The `decision_request` notification
   carries `data={entity, question}` (was `{entity}`); the `request_decision`
   action stays free-text (`{to, text}`). No `options`, no decision id.

The frontend renders the bubble **inside the chat rail**, composing with 037's
drawer + unread badge + aria-live (ADR 0022) for narrow-width attention — it does
not build its own panel.

## Alternatives rejected

- **A `DecisionStore` table (mirror `ModeRequestStore`).** Creates a *second*
  source of truth for "is a decision open?" that must stay in lockstep with
  `awaiting_decision` across re-asks, restarts, and scheduler nudges — a real
  drift/double-resume hazard — to buy a per-decision history nobody asked for (the
  audit log already records each `request_decision_sent`). Rejected: cost without
  a requirement.
- **Overload `mode_requests` with `kind='decision'`.** Reuses the store but forces
  a free-text Q&A through an approve/deny + `requested_mode` schema it doesn't fit;
  the lifecycle (`approve`/`deny`) doesn't map to "the user replied with prose."
  Rejected as a semantic mismatch.
- **Id-keyed endpoint (`/api/decision/{id}/reply`).** Matches the mode/vault URL
  convention but needs the rejected store and contradicts the one-deep,
  entity-addressed reality. Rejected.
- **A dedicated `pm.resume_decision()` or inline clear→send→route in `app.py`.**
  Forks the unpark+resume logic that already lives in `_send_to_entity`. Rejected
  in favour of delegating to the message path.
- **A standalone bell-style decisions panel.** Duplicates 037's drawer + unread
  badge (ADR 0022) and overlaps 039's attention router. Rejected.
- **Multi-choice plumbing now.** The producer can't emit `options` (the action is
  free-text only); building the renderer is plumbing for a signal that never
  arrives. Deferred to a clean follow-up if a maestro ever needs structured
  choices.

## Consequences

- The web decision channel is **deliberately asymmetric** with mode/vault: no
  store, no row id, entity-keyed. A future reader asking "why is there no
  `DecisionStore` like `ModeRequestStore`?" is answered here — it's the one-deep,
  free-text shape, not an oversight.
- The web reply **reuses `_send_to_entity` verbatim**, so the phase-confirmation
  side effect (ADR 0019) and the load-bearing clear-before-send ordering come for
  free and can't drift from the Telegram path.
- Cost is **one nullable column + one migration (032)** and two thin endpoints —
  no new store class, model, or lifecycle.
- The question becomes **recoverable on reload** (the gap that motivated the
  ticket), via a scan, not a table.
- **Migration-number race:** 032 is correct against origin/main at authoring time
  (031 highest); re-verify and renumber at ship time if a parallel worktree took
  it.
- If a real need for decision *history* or *multiple concurrent* decisions per
  maestro ever appears, this is reversible — but it would be a deliberate model
  change (a store + an id), recorded by a superseding ADR, not a silent drift.
