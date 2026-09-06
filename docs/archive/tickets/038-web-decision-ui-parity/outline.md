# Outline — Ticket 038: Web decision-UI parity (029 → web)

Build order for the single-PR (direct lane) implementation. Bottom-up:
persistence → producer → resume → endpoints → frontend → tests. Each step is
independently runnable/greenable; the frontend is verified by a real-iPad
re-smoke (curl-200 can't validate Safari mount).

## 1. Persist the question (migration + model + store)
- `bus/migrations/032_entity_decision_question.sql` — `ALTER TABLE entities ADD
  COLUMN IF NOT EXISTS last_decision_question TEXT` (nullable). *Re-check the
  number vs origin/main at ship time.*
- `models/entity.py` — `last_decision_question: str | None = None` (next to
  `awaiting_decision`).
- `bus/entity_store.py` — add the column to the upsert column list + values +
  the `_row_to_entity` restore (mirror `awaiting_decision` exactly).

## 2. Producer: capture + carry the question
- `process/message_dispatcher.py` (request_decision→user branch, ~L466-472):
  - set `entity.last_decision_question = text` alongside `awaiting_decision=True`;
  - enrich the notify: `data={"entity": entity_name, "question": text}`.
- Confirm the Telegram channel ignores the extra `data` key (reads `.text`).

## 3. Unpark: null the question on clear
- `process/manager.py` `clear_awaiting_decision` (~L225) — also set
  `last_decision_question = None` before persist.

## 4. Endpoints (`web/app.py`)
- `POST /api/decision/{entity}/reply` (`Depends(require_token)`):
  - 400 if `reply` empty; build `Command("message", entity, reply)`;
    `await command_dispatcher.dispatch_command(cmd, actor="web:user")`;
  - persist round-trip like `/api/command` when `not result.routed`;
  - return `{ok: True, entity, text: result.text}`; map unknown entity → 404;
    surface the `<parked at gate>` no-op rather than reporting success.
- `GET /api/decisions/pending` (`Depends(require_token)`): scan
  `process_manager.entities` (or the manager's accessor) for `awaiting_decision`
  → `{"decisions": [{"entity": name, "question": last_decision_question}]}`.

## 5. Frontend (`web/templates/landing.html` — single render path)
- SSE switch (`~L722-733`): add `else if (evt.kind === 'decision_request' &&
  evt.data) appendDecisionBubble(evt.data);` (if/else-if chain — won't hit the
  catch-all).
- `appendDecisionBubble(data)`: clone `appendModeRequestBubble` (L419-470);
  replace allow/deny actions with a free-text reply field + Send; escape
  `data.question` via top-level `escapeHtml` (L246); POST JSON `{reply}` to
  `/api/decision/${data.entity}/reply` (Bearer token from sessionStorage);
  on ok → collapse to "answered" + append `result.text` as an agent line;
  on error → re-enable + error line; key the node by `data.entity` (a re-ask
  supersedes a stale bubble).
- On load: `fetch('/api/decisions/pending')` → seed an in-rail bubble per row
  (recovery). Render inside the chat rail so 037's drawer + unread badge +
  aria-live (ADR 0022) carry narrow-width attention.
- `static/landing.css`: one reply-field hook (e.g. `.mode-req__reply`); reuse the
  rest of `.mode-req__*`.

## 6. Tests (`tests/web/…`, mirror existing endpoint tests)
- `test_decision_reply_*`: 401 (no/bad token); 200 + `dispatch_command` awaited
  with the expected `Command`; 404 (unknown entity); 400 (empty reply).
- `test_decisions_pending`: returns awaiting entities + their question; empty when
  none awaiting.
- `test_request_decision_persists_question`: producer sets
  `last_decision_question` + enriched `data`.
- `test_clear_nulls_question`: `clear_awaiting_decision` nulls the field and (for
  a maestro) still flips `confirmed_with_user`.

## 7. Verify
- `ruff check src/ tests/ && ruff format --check src/ tests/`
- `PYTHONPATH=src pytest -m "not integration"` (worktree note: editable install
  pins to MAIN src — `PYTHONPATH=src` overrides for plain runs).
- Deploy + **real-iPad re-smoke**: trigger a maestro `request_decision`, answer it
  from the iPad (bubble in the 037 drawer), confirm the maestro unparks; reload
  mid-decision and confirm `/api/decisions/pending` re-seeds the question.

## Sequencing / rebase notes
- **Shares `src/hive/web` with 037/039** (both in progress) → not a logical
  blocker, but rebase if a later PR; the fleet merges one at a time.
- **Composes with 037** (ADR 0022): the bubble renders in the chat-rail drawer and
  relies on 037's unread badge + aria-live for narrow-width. If 038 lands before
  037, the bubble sits in the (still-hidden) rail on iPad portrait — acceptable
  transient; 037 is the do-first foundation and the re-smoke runs on the combined
  surface.
