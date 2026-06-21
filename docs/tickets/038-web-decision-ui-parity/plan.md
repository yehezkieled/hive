# Plan — Ticket 038: Web decision-UI parity (029 → web)  (issue #209)

**Lane:** direct (single PR). The work is one coherent feature whose layers are
**sequential** — persistence → producer → resume → endpoints → frontend — so
fan-out parallelism buys nothing; it ships as one PR with one real-iPad re-smoke.
Build order and per-step detail in [`outline.md`](outline.md); the chosen approach
and rejected alternatives in [`design.md`](design.md) / [ADR 0023](../../adr/0023-decision-channel-entity-keyed.md).

## Files this Ticket creates / modifies

| Path | Op | Step |
|------|----|------|
| `src/hive/bus/migrations/032_entity_decision_question.sql` | **create** | 1 — add nullable `last_decision_question TEXT` (re-check number at ship) |
| `src/hive/models/entity.py` | modify | 1 — `last_decision_question: str \| None = None` |
| `src/hive/bus/entity_store.py` | modify | 1 — persist + restore the column (mirror `awaiting_decision`) |
| `src/hive/process/message_dispatcher.py` | modify | 2 — set the field + enrich `data={entity, question}` in the `request_decision→user` branch |
| `src/hive/process/manager.py` | modify | 3 — `clear_awaiting_decision` also nulls the field |
| `src/hive/web/app.py` | modify | 4 — `POST /api/decision/{entity}/reply` + `GET /api/decisions/pending` |
| `src/hive/web/templates/landing.html` | modify | 5 — SSE branch + `appendDecisionBubble` + pending-on-load seed |
| `src/hive/web/static/landing.css` | modify | 5 — one reply-field hook (reuse `.mode-req__*`) |
| `tests/web/test_app.py` (or peer) | modify/create | 6 — reply + pending endpoint tests |
| `tests/process/…` (dispatcher + manager) | modify/create | 6 — producer-persists + clear-nulls tests |

## Verification
- `ruff check src/ tests/ && ruff format --check src/ tests/` (CI runs both as
  separate gates).
- `PYTHONPATH=src pytest -m "not integration"` green (worktree: `PYTHONPATH=src`
  overrides the editable install that pins to MAIN src).
- Endpoint tests assert: 401 (no/bad token), 200 + `dispatch_command` awaited with
  the expected `Command`, 404 (unknown entity), 400 (empty reply); pending returns
  awaiting entities + question; producer sets `last_decision_question` + enriched
  `data`; `clear_awaiting_decision` nulls it and (maestro) flips `confirmed_with_user`.
- **Deployed real-iPad re-smoke** (JS-rendered → an actual Safari check, not
  curl-200): trigger a maestro `request_decision`; answer it from the iPad (bubble
  in the 037 drawer); confirm the maestro unparks and proceeds; reload mid-decision
  and confirm `/api/decisions/pending` re-seeds the question.

## Out of scope
- Multi-choice / structured decisions (producer emits free-text only — a clean
  future ticket if a maestro ever offers `options`).
- A standalone decisions panel (037 drawer + unread badge owns narrow-width; 039
  owns the attention router).
- `decision_resolved` cross-tab SSE event; editable-plan-before-approve;
  Telegram-side changes (029 already works there).

## Cross-cutting impact (declared upfront)
- **CONTEXT.md** — **Decision request** glossary entry added (decision vs approval).
  *Already committed in this run.*
- **ADR 0023** — entity-keyed / question-on-entity-row divergence. *Already
  committed in this run.*
- No `README.md` / `DEPLOYMENT.md` change — no new service, port, or env var.

## Build handoff
Single PR on a `ticket-038/…` branch, target `main`, squash-merge, that **closes
#209**. Validation gate before merge: `ruff check src/ tests/ && ruff format
--check src/ tests/ && PYTHONPATH=src pytest -m "not integration"`. run-ticket
ends here (planning only) — the implementation is a separate build step.

## Migration / ADR number watch
`032` (migration) and `0023` (ADR) are correct against origin/main at authoring
(highest committed: migration 031, ADR 0022). Numbers race across parallel
worktrees — **re-verify at ship time**; if taken, bump and fix refs (only mine,
not origin's).
