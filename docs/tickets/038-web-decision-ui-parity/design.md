# Design — Ticket 038: Web decision-UI parity (029 → web)

Chosen approach for porting the 029 maestro→user decision channel to the web.
Grounded in [`research.md`](research.md); the keystone storage/keying decision is
recorded in [ADR 0023](../../adr/0023-decision-channel-entity-keyed.md). The
responsive surface is owned by [ADR 0022](../../adr/0022-responsive-touch-shell-contract.md)
(Ticket 037) — 038 composes with it, it does not rebuild it.

## Guiding principle

**Mirror the proven `mode_request` rails where they fit; diverge only where the
decision channel is structurally different.** Hive already unparks + resumes a
maestro from a user reply (`_send_to_entity`: clear → send → route,
`dispatch.py:645-648`). 038 is a *delivery-layer* ticket: carry the question to
the browser, give it an interactive surface, make it recoverable on reload.

## The six resolved levers

| # | Lever | Decision | Why (1-liner) |
|---|-------|----------|---------------|
| 1 | Question storage | **Entity row** — nullable `last_decision_question` col (migration 032), set where `awaiting_decision=True` is | one-deep channel; keep `awaiting_decision` the single source of truth (ADR 0023) |
| 2 | Endpoint keying | **Entity-keyed** `POST /api/decision/{entity}/reply` | `{entity}` is always a maestro; matches `clear_awaiting_decision(entity_name)` |
| 3 | Resume wiring | **Thin wrapper** → `dispatch_command(Command("message", entity, reply))` | reuse `_send_to_entity`'s clear→send→route; never fork it |
| 4 | Resolution | **Client-side** — disable + pending state on submit, render the maestro reply as a chat line; no new SSE event | mirrors `mode_request`; cross-tab is out of scope |
| 5 | Surface | **Single in-rail bubble** + `/api/decisions/pending` seeds the rail on load; narrow-width via 037's drawer + unread badge + aria-live | compose with ADR 0022 / 037; don't duplicate it or 039 |
| 6 | Multi-choice | **Out** — pure free-text | producer can't emit `options` today; clean future ticket |

## End-to-end flow (the round-trip 038 completes)

```
 MAESTRO (otter)                      HIVE backend                         WEB (landing.html, in chat rail)
 ───────────────                      ────────────                         ────────────────────────────────
 request_decision{to:user,            message_dispatcher (L466-472):
   text:"reuse auth table            ┌─ set entity.last_decision_question  ← NEW (was: nothing)
    or new sessions table?"} ───────▶│  set awaiting_decision=True
                                      │  _notify(kind="decision_request",
                                      │    data={entity, question, ...})    ← NEW: question now in data
   (turn ends, parked)               └─ persist
                                            │ SSE frame {kind, data}
                                            ▼
                                                              appendDecisionBubble(data):  ← NEW renderer
                                                                question + free-text reply field + Send
                                                                (trips 037 unread badge + aria-live)
                                            ┌──────────────────── user types "reuse auth", Send
                                            ▼                     POST /api/decision/otter/reply {reply}
 (resumes, reads reply,            app.py (NEW endpoint, thin wrapper):
  proceeds or re-asks) ◀───────────  dispatch_command(Command("message","otter",reply))
                                       └─ _send_to_entity: clear_awaiting_decision → send_to_entity → route
                                          (clear also nulls last_decision_question)
                                            │ returns maestro's turn output
                                            ▼
                                                              bubble collapses → "answered";
                                                              maestro reply appended as agent line
```

On a fresh load / reconnect: `GET /api/decisions/pending` scans entities with
`awaiting_decision=True`, returns `[{entity, question}]`, and the frontend seeds
each as an in-rail bubble (+ unread badge) — the SSE-drop recovery requirement.

## What changes, by layer

**Backend — producer (`process/message_dispatcher.py:466-472`)**
- Set `entity.last_decision_question = text` alongside `awaiting_decision = True`.
- Enrich the notification: `data={"entity": entity_name, "question": text}` (was
  `{entity}` only). `_notify` fans to Telegram too, which reads `.text` — extra
  `data` keys are ignored there (confirm).

**Backend — model + store + migration**
- `models/entity.py`: add `last_decision_question: str | None = None`.
- `bus/entity_store.py`: persist + restore the column (mirror `awaiting_decision`).
- `bus/migrations/032_entity_decision_question.sql`: `ADD COLUMN IF NOT EXISTS
  last_decision_question TEXT` (nullable). **Re-check the number at ship time —
  migration numbers race across worktrees** (031 highest on origin/main now).

**Backend — unpark (`process/manager.py:214-241`)**
- `clear_awaiting_decision` also sets `last_decision_question = None` (tidiness;
  the pending scan already filters on `awaiting_decision`).

**Backend — endpoints (`web/app.py`)**
- `POST /api/decision/{entity}/reply` (`Depends(require_token)`): read `{reply}`
  from body; `dispatch_command(Command("message", entity, reply), actor="web:user")`;
  persist like `/api/command` (`not result.routed`); return `{ok, entity, text}`.
  404/400 if `entity` unknown or `reply` empty; surface the `<parked at gate>`
  no-op case rather than swallow it.
- `GET /api/decisions/pending` (`Depends(require_token)`): scan
  `process_manager.entities` for `awaiting_decision` → `{"decisions": [{entity,
  question}]}`. (No store query — mirrors the *shape* of `/api/mode-requests/pending`,
  not its backing table.)

**Frontend (`web/templates/landing.html` — the only render path)**
- New `else if (evt.kind === 'decision_request')` branch in `startNotificationStream`
  (`:722-733`) → `appendDecisionBubble(evt.data)`.
- `appendDecisionBubble`: clone `appendModeRequestBubble` (`:419-470`) but swap the
  allow/deny actions for a free-text `<input>`/`<textarea>` + Send; POST a JSON
  `{reply}` body (Content-Type: application/json, `Authorization: Bearer`, token
  from `sessionStorage`) like the vault-deny shape; on success collapse to
  "answered" + append the returned maestro reply as an agent line; on failure
  re-enable + error line. Escape the question with the top-level `escapeHtml`
  (`:246`). Key the bubble by `entity` so a re-ask supersedes a stale one.
- On load: `fetch('/api/decisions/pending')` → seed an in-rail bubble per row.
- **No new CSS class needed** beyond a reply-field hook; reuse `.mode-req__*`
  (`landing.css:518-583`). Render inside the chat rail so 037's drawer + unread
  badge + aria-live (ADR 0022) carry narrow-width attention.

**Tests** (mirror existing endpoint tests, `web/app.py` test patterns):
`/api/decision/{entity}/reply` — 401 (no/bad token), 200 + `dispatch_command`
asserted awaited with the right `Command`, 404 (unknown entity), 400 (empty
reply); `/api/decisions/pending` — 200 returns awaiting entities + question;
producer test — `request_decision` sets `last_decision_question` + enriched
`data`; `clear_awaiting_decision` nulls the field + (maestro) flips
`confirmed_with_user`.

## Cross-cutting / reference-doc impact
- **CONTEXT.md** — add a glossary entry sharpening **Decision request** (the 029
  free-text, entity-keyed, one-deep channel) vs an **approval** (mode/vault,
  row-id'd allow/deny). Done in this ticket.
- **ADR 0023** — the entity-keyed / question-on-entity-row divergence from the
  mode/vault store pattern. New.
- No README/DEPLOYMENT change (no new service, port, or env var).

## Alternatives rejected (full reasoning in ADR 0023)
- **New `DecisionStore` table** — dual source of truth with `awaiting_decision`;
  drift hazard for a one-deep channel; history nobody asked for.
- **Overload `mode_requests` (`kind='decision'`)** — approve/deny + `requested_mode`
  columns don't model a free-text Q&A.
- **`pm.resume_decision()` / inline clear→send→route** — forks logic that already
  lives in `_send_to_entity`.
- **Separate bell-style decisions panel** — duplicates 037's drawer+badge (ADR
  0022) and overlaps 039's attention router.
- **`decision_resolved` SSE event** — buys only cross-tab consistency `mode_request`
  itself doesn't have.
- **Multi-choice plumbing now** — the producer can't emit `options`; YAGNI.
