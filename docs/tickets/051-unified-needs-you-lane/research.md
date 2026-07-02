# 051 — Research

Answers to `questions.md`, from a six-reader parallel code sweep
(2026-07-02). Every claim carries file refs. **Read the premise
corrections first — the ticket's mental model is wrong in five places.**

## Premise corrections

1. **The needs-you UI lives entirely in the Jinja2 + htmx + vanilla-JS
   landing page** (`templates/landing.html`, ~1,790 lines, ~1,400 of
   inline JS). The React/JSX files (`static/dashboard/*.jsx`) are an
   unrelated observability tab (charts only, zero interrupts, zero SSE) —
   051 does not touch React unless it deliberately adds it.
2. **The approve/deny logic is copy-pasted FIVE times, not three**: the 3
   SSE bubbles (`landing.html:456-490`, `:520-549`, `:591-627`) plus the
   2 bell-popup row renderers (`:1476-1501`, `:1598-1623`) — and the whole
   popup chrome (position/open/close/outside-tap/Escape/resize) is
   duplicated verbatim between `initBellPanel` (`:1413-1529`) and
   `initGatePanel` (`:1535-1651`, whose comment admits "Mirrors
   initBellPanel").
3. **The "errored/blocked" surface does not exist today — it is net-new,
   not consolidation.** `EntityState.ERROR` renders as "dormant"/"idle"
   (`view_model.py:56-74`, `:252-272`), the header health pill is
   hardcoded `"all systems ok"` (`view_model.py:358`), no `errored`
   notification kind exists (the health check audits but never notifies,
   `manager.py:727-739`), and there is no GET or action endpoint.
4. **The gate bell is dead code on the live system.** `/api/gates/pending`
   filters `list_pending(default_maestro, kind="gate")`
   (`app.py:388-393`) but every gate row is created with
   `approver="user"` (`claude_adapter.py:86`, `pty_session.py:179`,
   never overridden in `lifecycle_manager.py:363-369`) — the popup always
   renders empty, and `tests/test_web_gate_endpoints.py:79` bakes the bug
   in. Gates are also **vestigial by design** post-029: `ExitPlanMode` +
   `AskUserQuestion` are bare-name-denied for both coordinator roles
   (`tool_policy.py:29-47`; ADR 0018 keeps the bridge only as a fallback
   net).
5. **The two mode-request surfaces show disjoint sets.** The bell fetches
   `list_pending(default_maestro)` (`app.py:319-324`) — lead→maestro
   escalations the *maestro* should resolve — while the SSE bubble fires
   only for `approver == "user"` rows (`approval_handler.py:99-113`).
   The badge also counts vault+mode combined but the popup lists only
   mode (`view_model.py:293-297` vs `landing.html:1440`), and the badge
   is server-rendered once, outside every htmx swap region — it never
   live-updates.

## Q1–Q2 — Current surfaces & stack

- **Stack:** FastAPI + Jinja2; htmx 2.0.4 polls five server-rendered
  partials — hero 30s, vault 15s, active 5s, idle 5s, dormant 30s
  (`landing.html:151,163,179,189,202`; endpoints `app.py:506-529`), all
  built by `build_landing_view_model` (`view_model.py:235`). Inline
  vanilla JS handles SSE, drawer, chat, bells (`landing.html:277-1666`).
- **Bell 1** `#bell-btn` "Pending approvals" (`landing.html:47-50`):
  badge = `approvals_count` = vault_pending + mode_pending
  (`view_model.py:293-297,357`); popup driven by `initBellPanel`
  (`:1413-1529`) → GET `/api/mode-requests/pending`, rows with
  Allow/Deny → POST `/api/mode-request/{id}/{approve|deny}`.
- **Bell 2** `#gate-btn` "Pending gates" (`landing.html:51-53`, no
  badge): `initGatePanel` (`:1535-1651`) → GET `/api/gates/pending`
  (always empty — premise correction 4), Approve/Deny → POST
  `/api/gate/{id}/{approve|deny}`.
- **3 bubbles**, dispatched from the SSE `onmessage` switch
  (`landing.html:1135-1152`):
  `appendVaultRequestBubble` (`:428-492`),
  `appendModeRequestBubble` (`:494-551`),
  `appendDecisionBubble` (`:557-633` — free-text reply → POST
  `/api/decision/{entity}/reply`; entity-keyed supersede `:563-565`;
  reseeded on load via `/api/decisions/pending`, `:1129-1134`).
  `vault_action_resolved` gets a plain system chat line (`:1144-1145`);
  every other kind — **including `gate`, `auto_bounce_failed`,
  `workflow_*`** — falls to the generic `[kind] text` line (`:1147`).

## Q3 — What 039 built

A per-card **boolean**, not a feed: `_is_awaiting` = `awaiting_decision`
OR `is_parked_at_gate` (`view_model.py:125-139`), org-prefix rollup
(`:142-159`), rendered as the `● you` badge (`_macros.html:72,81`,
`_partials/idle.html:4,8`) plus a "Waiting on me" CSS filter chip
(`landing.html:174-177,894-912`). It **deliberately excluded mode/vault**
("stay on the bell", comment `view_model.py:132`) and **deliberately
rejected SSE** in favour of the 5s htmx poll, because clears fire no
event and an SSE-driven badge would stick
(`docs/tickets/039-awaiting-you-fleet-view/design.md:13`). 051 unifies a
deliberate split — and inherits the same set-vs-clear trap.

## Q4–Q6 — Backend state, endpoints, keying (per kind)

| Kind | Pending state | GET (list) | POST (act) | Key |
|---|---|---|---|---|
| decision | `Entity.awaiting_decision` + `last_decision_question`, durable (`entity.py:229,235`; set `message_dispatcher.py:471-481`; cleared `manager.py:214-231` via `dispatch.py:495`) | `/api/decisions/pending` (`app.py:353-365`) | `/api/decision/{entity}/reply` — thin wrapper dispatching `Command("message")` (`app.py:327-351`) | **entity** (one-deep, ADR 0024) |
| mode | `mode_requests` row, `kind='mode_request'` (`mode_request_store.py:15-142`; created `approval_handler.py:59-114`) | `/api/mode-requests/pending` — **wrong approver filter** (correction 5) | `/api/mode-request/{id}/{approve\|deny}` (`app.py:367-385`; approve applies+persists `approval_handler.py:397-420`) | **row id** |
| vault | `vault_actions` row (`vault_store.py:24-196`; created `approval_handler.py:116-216`) | **NONE** — only the htmx vault partial (`app.py:511-514`, name hardcoded `view_model.py:290`) + the transient SSE bubble; **reload loses pending vault** | `/api/vault-action/{id}/{approve\|deny}` (`app.py:415-455`; approve runs the full payment lifecycle `approval_handler.py:218-376`) | **row id** (+ idempotency_key) |
| gate | same `mode_requests` table, `kind='gate'`, always `approver='user'` (`gate_coordinator.py:88-117`) + in-memory doorbell + `EntityState.GATED` | `/api/gates/pending` — **dead** (correction 4) | `/api/gate/{id}/{approve\|deny}` (`app.py:395-413`); web approve never passes `chosen_option` — ask-gate option pick is Telegram-only (`approval_handler.py:495-518`) | **row id**, ≤1 live gate per entity |
| errored | `EntityState.ERROR` (`entity.py:25`; set by bounce give-up `manager.py:491-528` and dead-adapter health check `manager.py:717-739`) | **NONE** (only raw `state` in `/api/status`) | **NONE** — recovery verb is the `/reset <entity>` command (`dispatch.py:819-836`) via generic POST `/api/command` (`app.py:172-191`); messaging an ERROR entity works but leaves `state == ERROR` forever | **entity** |

Existing aggregations, all partial: `approvals_count` (vault+mode,
`view_model.py:297`), `awaiting_you` (decision+gate, `:125-159`),
`ALERT_KINDS` (`dispatcher.py:35-43`). **No unified needs_you exists.**
Also: `list_pending(kind=None)` returns gate rows mixed into mode
listings/counts (they share one table, discriminated by `kind` —
migration `026_mode_requests_kind.sql`).

## Q8 — Delivery mechanics

- SSE = **full payloads**, unnamed frames, `kind` inside the JSON
  (`sse.py:65-75`; consumed via `es.onmessage`, `landing.html:1135`).
  Single emit point `ProcessManager._notify` (`manager.py:741-752`) →
  dispatcher → SSE broker gets **every** kind unfiltered.
- SSE is **lossy by design** — bounded 100-event queue drops oldest
  (`sse.py:48-62`); that's why the decisions reseed endpoint exists.
- The established per-kind pattern: SSE push (instant render) + pull
  "pending" GET (recovery) + per-row POST (resolve).
- **The architecture favours a server-side rollup in `view_model.py`
  delivered through the existing htmx poll** — same layer as
  `approvals_count`/`awaiting_you`, same set-and-clear correctness
  argument 039 already litigated. Cross-surface resolution (approve via
  Telegram while the web is open) self-heals under polling; an SSE-driven
  lane would strand items (no `decision_resolved` kind exists; other
  kinds' resolved events would need client bookkeeping).

## Q9 — Push / deep-link coupling (041/048)

- `ALERT_KINDS` = {decision_request, mode_request, vault_action_pending,
  workflow_completed, workflow_failed} (`dispatcher.py:35-43`). Gates and
  errored get **no push today**.
- Deep-links (`web_push.py:42-63`): needs-you kinds → `/?reply=<entity>`
  (opens drawer, pre-addresses composer — `landing.html:655-681`);
  run-ended → `/?focus=<entity>&run=…` (`[data-entity]` scroll +
  `.is-focused`; `run` is never read client-side).
- **Cold-open trap:** only decisions reseed into the drawer on load;
  mode/vault pending are reachable only via the bell popups. Delete the
  bells+bubbles without a full-kind reseed and a `?reply` push tap for a
  mode/vault request lands on a composer with **nothing actionable in
  view**. The lane must be the reseed.
- `?focus` needs `[data-entity]` + `.is-focused` kept on whatever
  surface replaces the cards' badge (`_macros.html:72`,
  `landing.css:1064`).
- `web_push.py`'s kind switch has **no else branch** — adding a kind to
  `ALERT_KINDS` without extending it → `UnboundLocalError` at `:63`,
  swallowed by the dispatcher (`dispatcher.py:79-87`): push dies
  silently.
- Kind-name consumers that break on renames: WebPushChannel (names +
  data shapes), Telegram alert toggle (`bridge.py:146`), email digest
  (`email.py:90`), emit-side tests. **Keep SSE kinds stable.**

## Q7, Q10 — Gates post-029; errored actions

Covered in the table + corrections: gates are a vestigial fallback net
(plan/ask only — the `permission` kind was never detectable, ADR 0005;
those jams route to auto-bounce, `manager.py:406-407`). Errored entities
are rare-but-deliberate human-action-required parks (bounce give-up,
`manager.py:491-510` — "so it reads as visibly broken") that today park
**invisibly**; `auto_bounce_failed`'s text literally ends "Needs you."
(`manager.py:519-523`) yet has no bubble, no bell, no push.

## Q12 — Test surface

- **Harness patterns:** endpoint tests = FastAPI `TestClient(create_app(…))`
  + MagicMock PM (real dict `entities`, AsyncMock facades, monkeypatched
  `WEB_TOKEN`); view-model tests = direct `build_landing_view_model` with
  a hand-rolled `_FakePM` (never MagicMock for predicates — truthy-Mock
  pitfall documented in `tests/web/test_view_model_workflow.py:87-89`);
  template asserts = Jinja render of the partial; DB flows = conftest
  store fixtures on the pgvector testcontainer. No `integration` marker
  for any of these.
- **Certain breaks:** `test_web_dashboard.py:65-80` (gate-bell markup),
  `test_web_landing.py:88-107,133-145` (`approvals_count`,
  `vault.pending_approvals`), `tests/web/test_view_model_awaiting.py`
  (15 tests pinning `awaiting_you` + `is-awaiting` markup),
  `test_web_ipad_polish.py:78-84` (pins CACHE_VERSION `hive-v6` — bumps
  with any landing.css/JS ship; serially edited in 043/048).
- **Survive if per-kind POST endpoints are kept:** the four endpoint test
  files (they pin exact routes/shapes). `/api/mode-requests/pending` has
  zero coverage today.
- **No JS testing exists** (no package.json/jest/vitest/playwright; JSX
  asserted only as script-tag presence). Browser behaviour is
  verification-gated on the deployed iPad smoke — the declared boundary
  (`tests/web/test_view_model_awaiting.py:9-12`).
- **Conventions for new tests:** `tests/web/test_view_model_<topic>.py` +
  `tests/test_web_<topic>_endpoints.py`; contract-shape key-pin idiom
  (`test_web_landing.py:85-100`); route naming = collection-plural GET
  (`/api/needs-you`?) vs resolve-singular POST — the pattern all four
  existing pairs follow.

## Q11, Q13 — Contract with 052/053; scope check

- 052/053 don't exist yet — the mount contract is a design decision, but
  the code constrains it: the lane must live in landing-page world
  (Jinja partial + htmx region + shared JS action handler), and the hero
  needs a count + calm/loud state from the view model.
- **Scope check:** no new approval mechanics needed — every act-POST
  exists. But three net-new pieces surfaced: (a) an errored feed source
  (state scan + an action), (b) a vault pending JSON/read path (closes a
  real reload-loss gap), (c) the gate/mode approver-filter fix (bug fix
  inside the consolidation). Each is a read-path or bug fix, not a new
  mechanic — within the ticket's spirit; flagged for design.
