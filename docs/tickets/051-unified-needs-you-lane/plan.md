# Plan -- Ticket 051: Unified needs-you lane  (issue #259)

> Single-PR (direct-lane) implementation of the server-side `needs_you` rollup +
> one Jinja lane partial + one delegated JS handler, on the existing per-kind POST
> endpoints. Grounded in `design.md` (D1–D10) and `outline.md` (steps 1–7). Op is
> create / modify / delete; Step maps to the `outline.md` step that touches the file.

## Files this Ticket creates / modifies

| Path | Op | Step |
|---|---|---|
| `src/hive/web/view_model.py` | modify | 1, 2 |
| `src/hive/web/templates/_partials/needs_you.html` | create | 3 |
| `src/hive/web/templates/_macros.html` | modify | 3 |
| `src/hive/web/templates/landing.html` | modify | 3, 5, 6 |
| `src/hive/web/app.py` | modify | 3, 5 |
| `src/hive/web/static/landing.css` | modify | 3, 5 |
| `src/hive/web/static/needs_you.js` | create | 4 |
| `src/hive/web/static/service-worker.js` | modify | 7 |
| `tests/web/test_view_model_needs_you.py` | create | 7 |
| `tests/test_web_needs_you_endpoints.py` | create | 7 |
| `tests/test_view_model_awaiting.py` | modify | 7 |
| `tests/test_web_dashboard.py` | modify | 7 |
| `tests/test_web_gate_endpoints.py` | modify | 7 |
| `tests/test_web_landing.py` | modify | 7 |
| `tests/test_web_ipad_polish.py` | modify | 7 |

**Per-file notes (what the op is):**

- **`view_model.py`** — add pure `build_needs_you(...)` builder: scan
  `process_manager.entities` for `awaiting_decision` (→ decision items) and
  `state == EntityState.ERROR` (→ errored items, step 2); UNION
  `mode_request_store.list_pending(default_maestro)` **and** `list_pending("user")`,
  split each on `row['kind'] == 'mode_request'` (drop `gate`), dedupe by `id`; read
  `vault_store.pending("vault")`. Normalize every item onto the full-dotted `entity`,
  synthesize per-kind `summary`, pre-sort by `priority`, return `view["needs_you"] =
  {count, loud, items[]}`. Also **drop the `is_parked_at_gate` branch** from
  `_is_awaiting` (gate dead, D2/D8); keep the rest of `_is_awaiting`/`_awaiting_rollup`
  for the coexisting 039 card badge.
- **`_partials/needs_you.html`** (new) — calm/loud branch on `view.needs_you.count`;
  `count == 0` → one calm line `✓ all clear` (D10, the only empty string 051 owns);
  else loop `items` → call the macro.
- **`_macros.html`** — add `needs_you_item(item)`: `<article class="nyi"
  data-nyi-entity=… data-kind=… data-id=…>` with a `.nyi__head` header (open-tab
  target, D9) and a `.nyi__actions` block switched on `item.action`
  (`reply`/`approve_deny`/`reset`; `.nyi__reset` visually distinct + confirm, D3).
- **`landing.html`** — add the polled region `<section class="needs-you"
  hx-get="/api/landing/needs-you" hx-trigger="every 5s, needs-you:refresh from:body"
  hx-swap="innerHTML">` (step 3); include `needs_you.js` (step 4); **delete** `#bell-btn`
  + `initBellPanel`, `#gate-btn` + `initGatePanel`, the three SSE bubble builders
  (`appendVaultRequestBubble`/`appendModeRequestBubble`/`appendDecisionBubble`), the
  triplicated popup chrome, the three empty strings + `#awaiting-empty` machinery, the
  stale `approvals_count` bell badge (step 5); replace the `es.onmessage` bubble
  dispatch with a needs-you-kind → `needs-you:refresh` nudge and add
  `needs-you:refresh from:body` to the active/idle regions' `hx-trigger` (step 6).
- **`app.py`** — add `GET /api/landing/needs-you` via the existing `_build_view()`
  factory (step 3); **delete** `GET /api/gates/pending` / `api_gates_pending` (step 5).
  **Keep** all POST act routes, including the gate POSTs (D2/D5).
- **`landing.css`** — add lane styles + wire the existing dead `.state-dot--error` to
  errored items (step 3); **remove the dead `.bell*` rules** (step 5).
- **`needs_you.js`** (new) — one delegated `[data-action]` listener on `document`,
  scoped to `.nyi__actions`, small per-kind switch over the existing POSTs
  (`/api/decision/{entity}/reply`, `/api/mode-request/{id}/{approve|deny}`,
  `/api/vault-action/{id}/{approve|deny}`, `/api/command {"/reset <entity>"}`), reusing
  the `localStorage['hive_web_token']` bearer; on 2xx →
  `htmx.trigger(document.body,'needs-you:refresh')`. MUST NOT swallow header taps (D9).
- **`service-worker.js`** — bump `CACHE_VERSION` to `hive-v7`. **051 owns this bump;
  052/053 rebase onto it, they must NOT re-bump.**
- **`tests/web/test_view_model_needs_you.py`** (new) — `needs_you` key + per-kind item
  shape + `entity`-is-full-dotted-address + org-prefix filter + errored synthesized
  summary. Hand-rolled `_FakePM`, **never a bare `MagicMock`** (truthy-Mock pitfall).
- **`tests/test_web_needs_you_endpoints.py`** (new) — `GET /api/landing/needs-you`
  renders + auth; **DB-backed aggregation** seeding a real pending vault row **and both
  a lead→maestro AND a maestro-own mode row**, asserting all three appear and that the
  lead→maestro row lands under the right maestro's org-prefix filter (D2/D9).
- **`test_view_model_awaiting.py`** — delete/rewrite
  `test_gate_parked_sets_flag_without_decision` (pins the dropped gate branch); the
  other 10 tests survive.
- **`test_web_dashboard.py`** — delete `TestGatePanel` (pins `gate-btn`/"Pending
  gates"/`/api/gates/pending`).
- **`test_web_gate_endpoints.py`** — remove the GET dead-filter assertion; keep the
  POST act-path tests.
- **`test_web_landing.py`** — ADD `needs_you` alongside existing keys; update
  `test_vault_pending_counted` to assert the lane count (the `approvals_count` bell
  badge is removed).
- **`test_web_ipad_polish.py`** — update the CACHE_VERSION pin to `hive-v7`.

## Verification

**Lint / format (both gates fail independently, run both):**

```
ruff check src/ tests/ && ruff format --check src/ tests/
```

**Test suite (exclude integration; git-ship's bare pytest pulls integration in, so
run this form yourself):**

```
pytest -m "not integration"
```

Must be green, with the new/edited test files in particular:
- `tests/web/test_view_model_needs_you.py` (new — rollup shape, full-dotted `entity`,
  org-prefix filter, errored synthesized summary; hand-rolled `_FakePM`).
- `tests/test_web_needs_you_endpoints.py` (new — `GET /api/landing/needs-you` +
  auth + DB-backed union aggregation, both mode scopes + a real vault row, lead→maestro
  lands under the right org-prefix filter).
- `tests/test_view_model_awaiting.py` (gate-branch test deleted/rewritten; 10 survive).
- `tests/test_web_dashboard.py` (`TestGatePanel` deleted).
- `tests/test_web_gate_endpoints.py` (GET dead-filter assertion removed; POSTs kept).
- `tests/test_web_landing.py` (`needs_you` key added; `test_vault_pending_counted`
  asserts the lane count).
- `tests/test_web_ipad_polish.py` (CACHE_VERSION pin = `hive-v7`).

**CI gate:** confirm the real CI run is green by run-ID before merge (scoped local runs
miss failures; the Hive repo has no required-status checks, so `--auto` merges instantly
— watch CI explicitly, don't rely on it gating).

**Deployed iPad re-smoke (S10 DoD — this is a live web-behaviour change, three behaviours
have no unit coverage, Q15/D-risks):** deploy to `main`, `systemctl --user restart
hive.service`, then on a **real installed PWA** over the Tailscale IP / HTTPS (`:10000`),
not loopback and not a Safari tab:
1. **Delegated handler survives the 5s swap** — an action button still works after the
   region has re-polled several times (per-row listeners would have been wiped).
2. **Header-vs-action tap split (D9)** — tapping `.nyi__head` does not trigger an action
   (it stays a bindable open-tab target for 052/053); tapping an action control acts.
3. **Calm empty state (D10)** — with nothing pending, the lane shows one `✓ all clear`.
4. **Cross-surface lockstep (D8)** — answering an item heals both the lane and the 039
   card badge on the **same tick** (no visible disagreement window).
5. **`/reset` UX guard (D3)** — the errored recover button has its confirm affordance
   and is visually distinct from approve/deny.
6. **Deep-links (D7)** — after a cold open, `?reply=<entity>` pre-addresses the chat and
   `?focus=<entity>` still lands on the 039 card (lane uses `data-nyi-entity`, not
   `data-entity`).

## Out of scope

Deferred pieces (from `design.md` § Deferred) and ticket non-goals — named here to keep
051's boundary sharp:

- **052's Stack-home layout / hero headline copy** ("N loops running", calm/loud
  framing) → **052.** 051 ships only the bare `✓ all clear` lane line and the reusable
  partial + view-model contract.
- **053's tab navigation wiring** (binding the item header → the right Work-view tab) →
  **053.** 051 ships the header as a bindable open-tab target with a stable
  `data-nyi-entity` anchor; it does not build the tab router.
- **Push delivery / `ALERT_KINDS` changes (041):** no new `needs_you` wire notification
  kind (rollup is a server-side view, not an event — would break the cross-surface
  `ALERT_KINDS` contract, D6); no adding `gate`/`errored` to `ALERT_KINDS`; no push-side
  deep-link repair for mode/vault in `web_push.py`. 051 normalizes `entity` **only inside
  the rollup**, never touching `web_push.py` or `ALERT_KINDS`.
- **New approval types / new act mechanics:** no unified `POST /api/needs-you/{key}/act`
  endpoint — the lane switches over the **existing** per-kind POSTs (D5). errored reuses
  the existing `/reset` command; vault/mode/decision reuse their existing routes.
- **Mid-run Workflow steering / new observability widgets** → sprint out-of-scope.
- **Splitting `errored` to its own follow-up ticket** → the designated 051 de-scope lever
  (D3), pulled **only if the window tightens** (it shares no data path with the other
  three kinds, so it lifts cleanly).

## Cross-cutting impact

Reference-doc edits declared up front (per CLAUDE.md — this Ticket changes underlying web
behaviour, so its doc impact is visible here, not discovered late):

- **`CONTEXT.md` glossary — DONE (landed with these artifacts).** Added three terms
  to the notification cluster: **Needs-you rollup** (the one server-side aggregation,
  self-healing by re-derivation each poll), **Needs-you lane** (the one polled component
  replacing the 2 bells + 3 SSE bubble renderers), **Needs-you item** (the uniform row;
  header = open-tab target, action = answer-in-place). Each notes `gate`'s drop and the
  errored `reset` action. The **Notification channel** entry is left unchanged — SSE
  stays a latency reducer, no wire-`kind` change (D6).
- **ADR 0028 — DONE (recorded with these artifacts).**
  [`0028-unified-needs-you-polled-rollup.md`](../../adr/0028-unified-needs-you-polled-rollup.md):
  needs-you as a polled server-side rollup, SSE demoted to a nudge, `gate` dropped, one
  canonical set reused by 052/053 — reversing 039's mode/vault-on-the-bell split. All
  three ADR tests hold: **hard-to-reverse** (052/053 wire to the `view["needs_you"]`
  shape + mount contract), **surprising** (poll-not-SSE despite the Web Push infra; gate
  deleted end-to-end), and a **real trade-off** (≤5s latency vs self-heal). Stays
  entity-carrying, consistent with ADR 0024. *(Number re-checked at ship — ADR numbers
  race across parallel worktrees.)*
- **INDEX.md** — flip the 051 row to its final state at close. **Not** a cross-cutting
  reference-doc edit, just registry hygiene.

---

The build is a **single PR** that closes the tracking issue (#259) on merge.
