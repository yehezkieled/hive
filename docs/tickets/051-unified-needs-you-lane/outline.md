# 051 — Outline

> Build order for the single-PR (direct-lane) implementation of the unified
> "needs-you" lane. This is the **structure / module sketch** — what each step
> changes, in which file(s), and how to verify it — not a file-op table (that
> lives in `plan.md`). Every step is grounded in `design.md` (decisions D1–D10)
> and `research.md` (premise corrections + the per-kind table). Deferred work
> (`design.md` § Deferred) is **not** outlined here.

The order below is dependency-driven: **data before markup before behaviour
before deletion**. The rollup (step 1) is the spine — nothing renders until it
exists. Errored (step 2) is carried as its own line item, per D3 (biggest single
build, the designated de-scope lever). Deletions (step 5) come **after** the new
lane is wired and green, so we never have a window with no needs-you surface.

```
  1. rollup (view_model)  ──▶  2. net-new sources (into the rollup)
            │                          │
            └──────────┬───────────────┘
                       ▼
  3. partial + macro + htmx region  ──▶  4. shared JS handler
                       │
                       ▼
  5. delete bells + bubbles + popup chrome + dead endpoint
                       │
                       ▼
  6. deep-link reseed + 039 lockstep reconciliation
                       │
                       ▼
  7. tests + CACHE_VERSION bump (hive-v7)
```

---

## Step 1 — `needs_you` rollup builder + item shape (the spine)

**What:** a new pure builder `build_needs_you(...)` in `view_model.py` that scans
existing state and returns the canonical set `view["needs_you"] = {count, loud,
items[]}`. This is the single source the lane, the 052 hero, and the 053 tab
filter all read (D9). The partial stays a dumb renderer — all normalization,
summary synthesis, and sorting happen here.

**Item shape (D4):** one uniform dict carrying the discriminated key so the two
irreconcilable key schemes (entity-keyed decision/errored vs row-id'd mode/vault)
coexist without unifying their values:

```python
{ kind, entity (full dotted address), id (int|None),
  key ("{kind}:{id or entity}"), summary, action, priority }
```

- `action` ∈ `{"reply", "approve_deny", "reset"}` — drives which macro body renders.
- `priority` ∈ `{P0,P1,P2}` — **server pre-sorts** the list; consumers must not
  re-sort (D9). Errored/decision high, mode/vault below.
- `entity` is **always** the full dotted address or maestro name, never a bare
  team — this is what lets the 053 org-prefix filter match every item (D4/D9).

**Sources wired in this step** (the three that already have a data path):
- **decision** — scan `process_manager.entities` for `awaiting_decision`; summary =
  `last_decision_question`; entity = `.name`; action = `reply`.
- **mode** — the **union of both approver scopes** (D2, resolves PC-5): call
  `mode_request_store.list_pending(default_maestro)` **and** `list_pending("user")`,
  filter each on `row['kind'] == 'mode_request'` (drops `gate`), dedupe by `id`.
  summary = `f"{requester} → {requested_mode}"` (+ `reason`); entity = `requester`
  (a live entity key); action = `approve_deny`.
- **vault** — read `vault_store.pending("vault")` straight into the rollup (this is
  the missing reseed read path, PC-8/step tie-in); summary = `reason` + money
  detail; entity = `requester`; action = `approve_deny`.

**Also in this step (D2/D8):** drop the `is_parked_at_gate` branch from
`_is_awaiting` — gate is dead end-to-end (PC-4), so the card-badge predicate stops
consulting it. Keep the rest of `_is_awaiting`/`_awaiting_rollup` for the coexisting
039 card badge (D8).

**Files:** `src/hive/web/view_model.py`.

**Risk / verify:** the **truthy-Mock pitfall** (Q15) — any predicate the rollup
consults (`awaiting_decision`, `state == EntityState.ERROR`, pending lists) must be
exercised with a hand-rolled `_FakePM` returning explicit values, **never a bare
`MagicMock`** (a Mock is truthy and silently flags every item). Verify the mode
union dedupes by `id` and that a lead→maestro row and a maestro-own row both land.
Confirm `entity` is the full dotted address for a lead requester (D2 says
`request_mode_change` raises on an unknown requester, so the value is a live key).
This step ships with its view-model tests (step 7) before anything renders.

---

## Step 2 — Errored source + action (net-new, first-class line item)

**What:** the one genuine build stretch — errored has **zero web surface today**
(PC-3) and shares **no data path** with the other three kinds, so it gets its own
step, its own budget, and its own tests (D3). Split out because it is the
designated **de-scope lever** if the window tightens (it lifts cleanly to a
follow-up ticket, D3/Deferred).

- **Source:** scan `process_manager.entities` for `state == EntityState.ERROR`
  (two existing producers set it: `_bounce_give_up`, `health_check`). Emit a
  needs-you item with a **synthesized summary** — no natural prompt text exists
  (Q6): `"loop errored — reset to recover"`. entity = `.name`; priority high.
- **Action = `reset`, not reply (hard constraint, Q8/D3).** The item's action
  descriptor is `reset`; the macro renders a distinct recover button. Nothing on
  the message path clears `ERROR` — only `/reset` (via `_execute_reset` →
  `state = IDLE`) does. A plain reply would run but never remove the item
  (un-dismissable). The button is wired to `POST /api/command {command:"/reset
  <entity>"}` in step 4.

**Files:** `src/hive/web/view_model.py` (the errored branch of `build_needs_you`).
Markup/action rendering is added in steps 3–4; this step owns the **read-path
derivation + synthesized summary**.

**Risk / verify:** genuinely new code with no existing surface to lean on — the
biggest correctness risk in 051. Verify with an explicit `_FakePM` that an
`EntityState.ERROR` entity produces exactly one item with `action == "reset"` and
the synthesized summary. Note the display state today collapses ERROR into
"dormant" (PC-3) — confirm the rollup reads `state` directly, not the collapsed
`_display_state`. `/reset` is safe on an already-IDLE or mid-turn entity (kills +
clears + sets IDLE unconditionally), so a transient health-check flip that briefly
shows/hides the button cannot corrupt state (D3).

---

## Step 3 — Jinja lane partial + macro + htmx region

**What:** the markup layer — one partial, one macro, one polled region (PC-1: this
is Jinja + htmx + vanilla JS, **not** React).

- **`_partials/needs_you.html`** (new): calm-or-loud branch on
  `view.needs_you.count`. `count == 0` → one calm line `✓ all clear` (D10, the
  only empty string 051 owns). Else, loop `items` and call the macro.
- **`needs_you_item(item)` macro** in `_macros.html` (new): renders one
  `<article class="nyi">` with:
  - `data-nyi-entity` (a **distinct** anchor, NOT `data-entity` — avoids the
    `?focus` collision with the 039 card, D7), `data-kind`, `data-id`.
  - a **`.nyi__head` header** = the open-tab target (052/053 bind navigation to it
    later, D9) — carries entity · kind · summary.
  - a **`.nyi__actions`** block = answer-in-place; per-kind body switched on
    `item.action`: `reply` → free-text input + send button; `approve_deny` → two
    buttons; `reset` → one recover button with a **confirm affordance**, visually
    distinct (`.nyi__reset`, D3).
- **landing.html region:** add the persistent wrapper
  `<section class="needs-you" hx-get="/api/landing/needs-you"
  hx-trigger="every 5s, needs-you:refresh from:body" hx-swap="innerHTML">`
  including the partial. 5s matches the urgent active/idle cadence (D1).
- **`GET /api/landing/needs-you`** in `app.py`: served via the existing
  `_build_view()` factory — no new plumbing, just a new fragment route.
- **CSS:** lane styles in `landing.css`; wire the already-existing (currently dead)
  `.state-dot--error` (`:987`) to errored items.

**Files:** `src/hive/web/templates/_partials/needs_you.html` (new),
`src/hive/web/templates/_macros.html`, `src/hive/web/templates/landing.html`,
`src/hive/web/app.py`, `src/hive/web/static/landing.css`.

**Risk / verify:** the region is innerHTML-swapped every 5s, so nothing inside it
may hold JS listeners (that's step 4). The `key` field must be rendered as a
stable DOM id so a mid-type reply input isn't clobbered across swaps (D4). Verify
the partial renders both branches (harness B: `build_landing_view_model` + a
hand-built Jinja `Environment`, Q15) and that the endpoint returns the fragment
under auth.

---

## Step 4 — Shared delegated JS action handler

**What:** the single behaviour layer — one delegated `[data-action]` listener that
survives the 5s swaps (the **single most important porting gotcha**, Q14/D5).
Per-row `addEventListener` (as today's `renderRows`/bubbles do) would be wiped on
every poll.

- **`src/hive/web/static/needs_you.js`** (new): one listener attached once on
  `document`, scoped to clicks on `[data-action]` **inside `.nyi__actions`**. It
  reads `data-kind`/`data-id`/`data-nyi-entity`/`data-action` and does a **small
  per-kind switch** over the **existing** POST endpoints (D5 — no unified endpoint):
  - `reply` → `POST /api/decision/{entity}/reply {reply}`
  - `approve`/`deny` (mode) → `POST /api/mode-request/{id}/{approve|deny}`
  - `approve`/`deny` (vault) → `POST /api/vault-action/{id}/{approve|deny}`
    (deny sends optional `{reason}`)
  - `reset` → `POST /api/command {command:"/reset <entity>"}` (after confirm)
  - on any 2xx → `htmx.trigger(document.body, 'needs-you:refresh')` → next poll
    re-derives the list, the answered item self-heals away (D1).
- **Must NOT swallow header taps** — `.nyi__head` is 052/053's open-tab target
  (D9). The handler is scoped to `.nyi__actions` only.
- **Lives in a shared module, not landing's inline `<script>`** — 052 rewrites the
  landing body, so a buried handler would silently die on the new home (D9). Both
  `landing.html` and the future 052 home include this file.
- **Auth:** reuse the existing bearer read from `localStorage['hive_web_token']`;
  every route is already `require_token`-guarded (D5) — no new auth work.

**Files:** `src/hive/web/static/needs_you.js` (new); `landing.html` includes it.

**Risk / verify:** delegation-survives-swap and the header-vs-action tap split have
**no JS test harness** (Q15) — they are **iPad-smoke-gated** on a real installed
PWA (step 7 verification note). Verify each per-kind POST fires the right route and
that a successful action triggers the re-poll nudge.

---

## Step 5 — Delete the 2 bells + 3 bubbles + duplicated popup chrome + dead endpoint

**What:** collapse the old duplicated surfaces now that the lane covers them
(PC-2: approve/deny hand-rolled 5×, popup chrome 3×). Done **after** steps 1–4 so
there's never a gap with no needs-you surface.

Delete from `landing.html`:
- `#bell-btn` popup + `initBellPanel` (+ its `renderRows`).
- `#gate-btn` popup + `initGatePanel` (+ its `renderRows`) — the gate surface is
  dead end-to-end (PC-4/D2).
- the three SSE bubble builders `appendVaultRequestBubble` / `appendModeRequestBubble`
  / `appendDecisionBubble` and their copy-pasted attention-routing side-effects.
- the triplicated popup open/close / outside-tap / Escape / resize chrome.
- the three empty strings (`#awaiting-empty` + two `.bell-popup__empty`) → replaced
  by the lane's single calm line (D10).
- the stale server-rendered `approvals_count` bell badge (frozen, disagreed with
  its own popup, PC-6).

Delete from `app.py`:
- `GET /api/gates/pending` (`api_gates_pending`) — always returned `[]` (PC-4).

**Keep (do not delete):** all POST act routes, **including the gate POSTs** and the
in-memory `is_parked_at_gate` runtime detector (an inert safety net, D2). Keeping
the POSTs keeps `test_web_gate_endpoints.py`'s act-path tests valid.

CSS: remove the dead `.bell*` rules in `landing.css`. (Note: `.bell-popup__*` /
`.gate-*` exist only as inline JS class strings, not CSS selectors — nothing to
delete for those in the stylesheet.)

**Files:** `src/hive/web/templates/landing.html`, `src/hive/web/app.py`,
`src/hive/web/static/landing.css`.

**Risk / verify:** this is the largest single-file churn and the shared-file rebase
hotspot (051/052/053 all touch `landing.html`). Delete only after the lane is green
so the surface is never absent. Run the full web + approval suite green after
deletion; re-smoke each interrupt type (decision/mode/vault) on the lane to confirm
nothing that used to bubble is now unreachable.

---

## Step 6 — SSE nudge + deep-link reseed + 039 lockstep reconciliation

**What:** rewire the live-update path and heal the two remaining consistency seams.

- **SSE demoted to a nudge (D1/D6):** replace the `es.onmessage` bubble dispatch
  (`landing.html:1135`) with a one-line "if `kind` in the needs-you whitelist →
  `htmx.trigger(body,'needs-you:refresh')`". **No wire `kind` changes** — SSE
  frames are unnamed, the whitelist is client-side (D6). The lane never renders
  from SSE; SSE is only a latency reducer.
- **Cold-open reseed (D7, closes PC-8):** the `needs_you` partial renders
  server-side on first page load, so **the lane IS the reseed** for every kind —
  including vault, which had no reseed path at all before. No `loadPendingDecisions`
  vault patch needed; the server rollup covers it.
- **039 timing lockstep (D8):** add `needs-you:refresh from:body` to the
  **active/idle regions'** `hx-trigger` too, so the coexisting 039 card badge heals
  on the **same tick** as the lane after an action — otherwise the card badge
  lingers until its own independent 5s timer, re-creating the "two surfaces
  disagree" class 051 exists to kill. One line per region.
- **`?focus` anchor stays on the card (D7):** because lane items use
  `data-nyi-entity` (not `data-entity`), `hiveDeepLink`'s
  `querySelector('[data-entity=…]')` keeps hitting the 039 card — **no change to
  `web_push.py` or the service worker**. `?reply=<entity>` needs no re-point either
  (it pre-addresses the chat input, doesn't query the lane).

**Files:** `src/hive/web/templates/landing.html` (SSE handler + active/idle
`hx-trigger` lines).

**Risk / verify:** confirm the SSE whitelist covers exactly the needs-you kinds and
that a frame triggers a re-poll (not a render). Verify — on the iPad re-smoke — that
answering an item heals both the lane and the 039 card badge on the same tick (no
visible disagreement window). Confirm `?reply=` and `?focus=` still land correctly
after a cold open.

---

## Step 7 — Tests + CACHE_VERSION bump

**What:** the test surface (server-side only — no JS harness exists, Q15) and the
one coordinated cache bump.

**New tests:**
- `tests/web/test_view_model_needs_you.py` (harness B): the `needs_you` key +
  per-kind item shape + `entity`-is-full-dotted-address + org-prefix filter +
  errored synthesized summary. Hand-rolled `_FakePM`, **never a bare MagicMock**
  (truthy-Mock pitfall).
- `tests/test_web_needs_you_endpoints.py` (harness A + DB-backed): `GET
  /api/landing/needs-you` renders + auth; **DB-backed aggregation** seeding a real
  pending vault row **and both a lead→maestro AND a maestro-own mode row**,
  asserting all three appear **and** that the lead→maestro row lands under the right
  maestro's org-prefix filter (D2/D9 — the one scope call that defines lane
  correctness). Session-scoped pgvector container, runs in the default suite.

**Tests to delete/rewrite (pin the dead surface):**
- `test_view_model_awaiting.py::test_gate_parked_sets_flag_without_decision` —
  delete/rewrite (it pins the dropped gate branch, D8). The other 10 survive.
- `test_web_dashboard.py::TestGatePanel` — delete (asserts `gate-btn`/"Pending
  gates"/`/api/gates/pending`).
- `test_web_gate_endpoints.py` GET-filter test — remove the dead-filter assertion;
  keep the POST act tests.
- `test_web_landing.py` — **ADD** `needs_you` alongside the existing keys; update
  `test_vault_pending_counted` to assert the lane count (the `approvals_count` bell
  badge is removed).

**CACHE_VERSION bump:** `service-worker.js` → `hive-v7` (CSS/JS changed). Update the
`test_web_ipad_polish.py` CACHE_VERSION pin. **051 OWNS this bump; 052/053 rebase
onto it and must NOT re-bump** (avoids the triple-bump rebase churn the sprint risks
call out).

**Files:** `tests/web/test_view_model_needs_you.py` (new),
`tests/test_web_needs_you_endpoints.py` (new), the four edited test files above,
`src/hive/web/static/service-worker.js`, `tests/test_web_ipad_polish.py`.

**Risk / verify:** run `ruff check` + `ruff format --check` + full
`pytest -m "not integration"` green. Then the **iPad re-smoke on a real installed
PWA** (Q15/D-risks) — the three behaviours with no unit coverage: delegated handler
survives the 5s swap, the header-vs-action tap split (D9), and the calm empty state.
`/reset` UX guard: confirm the errored button has its confirm affordance and is
visually distinct from approve/deny before ship.

---

## Not in this outline (deferred — see `design.md` § Deferred)

Do not build any of these in 051: push-side deep-link repair for mode/vault (041);
adding `gate`/`errored` to `ALERT_KINDS` (041); a new `needs_you` wire notification
kind (not built — the rollup is a server-side view); 052's hero headline copy (052);
053's tab navigation wiring (053 — 051 only ships the bindable header + `data-nyi-entity`
anchor); mid-run Workflow steering / new observability widgets (sprint out-of-scope).
Splitting `errored` to its own follow-up is the designated de-scope lever **only if
the window tightens** (D3).
