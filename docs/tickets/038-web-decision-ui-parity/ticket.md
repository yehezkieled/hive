# 038 — Web decision-UI parity (029 → web)

> The headline interaction feature: answer a maestro's mid-turn question from
> the iPad. Hive owns the hard half already (029 channel + `awaiting_decision`).

## What

Bring the 029 maestro→user conversational decision channel (ADR 0018) to the
web. Today a `request_decision` arrives via SSE but falls through to a plain
`[decision_request] …` system line — no bubble, no reply field, and the payload
carries only `{entity}` (not the question). Add: an enriched `decision_request`
payload (carry the question text + context), an `/api/decision/{entity}/reply`
endpoint backed by a durable store that calls `clear_awaiting_decision`, an SSE
**decision bubble** with an inline reply field, and (stretch) multi-choice
rendering.

## Why

Hive's core value is steering/deciding remotely, and under the "loop engineering"
direction (human rarely in the loop) the decision moment must be crisp and
reachable from the iPad. The backend machinery exists; this is the web delivery
layer. (Competitor scan rank #3.)

## Acceptance

- `decision_request` notification payload carries the question text (+ context),
  matching the richness of `mode_request` / `vault_action`.
- `POST /api/decision/{entity}/reply` clears `awaiting_decision`, forwards the
  reply to the maestro, and resumes it; durable store tested.
- A maestro decision renders on the web as an interactive bubble with a reply
  field (not a plain system line).
- `/api/decisions/pending` (or equivalent) so a fresh load shows outstanding
  decisions.
- Deployed re-smoke: a real maestro decision answered from the web unparks it.

## Non-goals

- Editable-plan-before-approve (Devin-style) — design toward, not this ticket.
- Multi-choice structured gate UI beyond a basic version — stretch.
- Telegram-side changes (029 already works there).

## Notes

Backend: `src/hive/web/app.py`, `process/message_dispatcher.py`,
`models/entity.py` (`awaiting_decision`). Frontend: `landing.html` SSE handler.
This is the backend-heavy ticket of S8 (not pure front-end).
