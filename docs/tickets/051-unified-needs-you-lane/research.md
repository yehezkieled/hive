# 051 — Research

> Fresh 9-reader parallel code sweep against live `src/`. Supersedes the
> 2026-07-02 six-reader pass. Feeds `design.md`. Every claim carries a `path:line`
> ref from the sweep. Where a fact the design needs is not in the sweep, it is
> marked **CONFIRM IN CODE**.

The ticket's mental model is right on the *duplication* count but wrong on several
*sources*. Read the premise corrections first — three of them change scope.

---

## Premise corrections

**PC-1 — No React. The lane is a Jinja2 partial + htmx-polled region + delegated
vanilla JS.**
The landing page (`/`) is a FastAPI + server-side Jinja2 app that refreshes live
regions with htmx 2.0.4; all interactivity is hand-written vanilla JS in one
`<script>` block. The React `.jsx` files under `static/dashboard/` are a *separate*
observability tab at `/dashboard` and touch none of the needs-you surfaces
(`app.py:68-91`, `landing.html:12`, `landing.html:277-1666`,
`dashboard.html:44-71`, `dashboard-mount.jsx:26-74`, `dashboard-shell.jsx:365-368`).
"One lane component" = `view_model.py` (data) + a new `_partials/*.html` (markup) +
inline delegated JS (interaction). **Not** a React component. (Q3.)

**PC-2 — "2 bells + 3 bubbles" is right, but the real duplication is larger: the
approve/deny row is hand-rolled FIVE times, popup chrome THREE times.**
The two bells (`#bell-btn`, `#gate-btn`, `landing.html:47-53`) and three SSE bubble
builders (`appendVaultRequestBubble:428`, `appendModeRequestBubble:494`,
`appendDecisionBubble:557`) are confirmed exactly. But the two-button approve/deny
row is independently implemented five times — the three bubbles plus
`initBellPanel.renderRows` (`landing.html:1460-1500`) and
`initGatePanel.renderRows` (`landing.html:1582-1622`); four share byte-identical
`.mode-req__btn` markup + disable→POST→resolve handler. The popup open/close /
outside-tap / Escape / resize chrome is triplicated (help/bell/gate,
`landing.html:1312-1333`, `:1420-1430`, `:1518-1528`, `:1542-1552`, `:1640-1650`;
`initGatePanel`'s own comment says "Mirrors initBellPanel"). Design for one
lane-item macro + one delegated handler, not "merge 5 parallel renderers."

**PC-3 — "Errored/blocked" is NET-NEW, not a consolidation. It has zero web
surface today.**
`EntityState.ERROR` (`entity.py:25,42`) is set at exactly two sites —
`_bounce_give_up` and `health_check` (`manager.py:505-508`, `:729-738`) — but
`_display_state` collapses it into the `dormant` bucket (`view_model.py:56-74`): an
errored maestro looks identical to a never-spawned one. No endpoint, no
notification kind, no renderer, no badge. The `.state-dot--error` CSS class
(`landing.css:987`) is dead — the template only ever receives the collapsed 3-state
display value (`_macros.html:31-36,77`, `view_model.py:183`). The header health
pill is hardcoded `{state:'active', label:'all systems ok'}` (`view_model.py:357-358`,
`landing.html:44-46`) and never reflects an errored entity. So 051 =
"consolidate 3 real surfaces (decision/mode/vault) + delete 1 dead one (gate) +
build 1 brand-new one (errored)." (Q8, Q16.)

**PC-4 — "Interactive gates (003)" is dead end-to-end. Do not add a gate item;
delete the dead popup + endpoint.**
Both emitting tools (`ExitPlanMode`, `AskUserQuestion`) are bare-name-denied to
leads *and* maestros post-029 (`tool_policy.py:29-47`,
`lifecycle_manager.py:107-118`, `claude_adapter.py:130-131`); the permission gate
is undetectable and never fires under bypassPermissions (ADR 0005 `:19-43`,
`gates.py:24`). So no gate row is producible in normal operation (ADR 0018
`:40-60,86-88` anticipated "at most a detect-and-dismiss guard"). Separately, the
web endpoint is *already* broken: gate rows are always created with `approver='user'`
(`pty_session.py:179,465-466`, `claude_adapter.py:86`, `gate_coordinator.py:88-101`,
never overridden in `lifecycle_manager.py:363-369`) but `GET /api/gates/pending`
filters `list_pending(default_maestro='otter', kind='gate')` (`app.py:388-393`,
`config.py:98`, `mode_request_store.py:57-82`) — the approver values never
intersect, so it always returns `{"gates": []}`. The startup reconcile path uses
the correct `approver='user'` (`approval_handler.py:540,559`, `__main__.py:299`),
so only the web endpoint is wrong. Recommendation: 051 drops the gate popup +
endpoint + the `is_parked_at_gate` branch of `_is_awaiting`, rather than migrating a
surface that shows nothing. (Q13.)

**PC-5 — "Mode approvals" are a disjoint-set bug: the bell popup and the SSE bubble
never show the same rows.**
Mode-request creation sets `approver` by role: maestro's own request →
`approver='user'`; lead→maestro escalation → parent maestro name
(`approval_handler.py:45-57`). `_notify(kind='mode_request')` fires **only** when
`approver=='user'` (`approval_handler.py:82-114`) — so the SSE bubble renders only
maestro-own requests. But `GET /api/mode-requests/pending` and the bell badge call
`list_pending(default_maestro='otter')` (`app.py:319-324`, `view_model.py:293-296`)
— the *lead→otter escalation* rows, which fire no notification at all. The two
surfaces are perfectly non-overlapping: the bell lists what never bubbles, the
bubble shows what never reaches the bell (`landing.html:1440-1448`,
`approval_handler.py:99-113`). 051 must pick **one** approver scope for the lane
(and decide whether lead→maestro escalations — addressed to a maestro, not the user
— belong in the *human's* lane at all). (Q1, Q4.)

**PC-6 — The bell badge (`approvals_count`) does not live-update and its count
disagrees with its own popup.**
`approvals_count = len(vault_pending) + len(mode_pending)` (`view_model.py:287-297,
357`) — it counts vault + mode, but the bell popup it decorates lists **mode only**
(vault has no bell-popup renderer). And the badge sits in the un-swapped header
`top-bar__right` (`landing.html:47-53`), outside all five htmx-polled regions
(`landing.html:151-202`), so it is frozen at full-page render and never updates
under htmx swaps — even though the vault card's *identical* count refreshes every
15s (`_partials/vault.html:20-24`). `approvals_count` cannot be reused as the hero
count; it is a stale vault+mode subtotal. (Q6, Q9.)

**PC-7 — There is no unified `needs_you` anything. Four disjoint notions of "needs
you" exist, inconsistent by design.**
(a) bell badge = vault+mode (`view_model.py:297`); (b) `_awaiting_rollup` card
filter = decision+gate only, *deliberately excluding* mode/vault ("Mode/vault
approvals (source C) stay on the bell, not the card", `view_model.py:132`); (c) SSE
bubbles = mode+vault+decision (`landing.html:1135-1152`); (d) `ALERT_KINDS` (push) =
decision+mode+vault + two run-ended kinds (`dispatcher.py:35-43`). No `/api/needs-you`,
no `needs_you` view-model key exists anywhere in `src/hive/web`. 051 must establish
ONE canonical needs-you set and drive lane + badge from it. Note 051 *overturns* the
documented 039 split at `view_model.py:132`. (Q9, Q10.)

**PC-8 — Vault has NO pending-list read endpoint. A page reload silently loses a
pending payment.**
`app.py` exposes `GET /api/mode-requests/pending` (`:319`),
`/api/decisions/pending` (`:353`), `/api/gates/pending` (`:388`) — but the only
vault routes are the two POST resolvers (`app.py:415`, `:431`). Pending vault
reaches the JS surface *only* via the transient SSE bubble; the cold-open reseed
(`loadPendingDecisions`, `landing.html:638-647,1131-1134`) seeds decisions only. A
reload — or a dropped SSE frame on a sleeping iPad tab — loses the pending vault
item entirely (it survives only as a non-actionable count on the vault card,
`_partials/vault.html:21`). The store method `vault_store.pending("vault")` already
returns the rows (`view_model.py:290`, name hardcoded to literal `"vault"`); it just
needs surfacing. (Q4.)

**PC-9 — Notification `data` keys are inconsistent across kinds, and mode/vault push
deep-links are already broken.**
decision uses `data['entity']`; mode/vault use `data['requester']`
(`message_dispatcher.py:472-476`, `approval_handler.py:106-113`, `:206-214`). Web
Push reads `data.get('entity','')` for the title and `/?reply=<entity>` url
(`web_push.py:37-53`), so a real mode/vault push renders " — approval needed" and
`/?reply=` (both empty); the tests only pass because they inject a synthetic
`{'entity':'otter'}` the real caller never sends (`test_web_push_channel.py:129-168`).
051's rollup should normalize every kind onto one actor field (`entity`) —
repairing the push deep-links as a drive-by. (Q11, Q12.)

---

## Answers, grouped by question

### Q1–Q3 — the current surfaces we're collapsing

**Q1 (the 2 bells).** `#bell-btn` ("Pending approvals", `initBellPanel`) fetches
`GET /api/mode-requests/pending` and renders mode-request rows; `#gate-btn`
("Pending gates", `initGatePanel`) fetches `GET /api/gates/pending`
(`landing.html:47-53`, `:1413-1529`, `:1535-1651`, `app.py:319-324`, `:388-393`).
Only `#bell-btn` carries a count badge (server-rendered `view.approvals_count`);
`#gate-btn` has none (`landing.html:47-50` vs `:51-53`). Coverage: bell = mode
(partial — only lead→otter rows, PC-5) + a vault-inclusive *count* it doesn't list;
gate = nothing (dead, PC-4). Neither covers decision or errored.

**Q2 (the 3 SSE bubbles).** All three are vanilla-JS in `landing.html`'s inline
script, dispatched by the `es.onmessage` switch on `evt.kind`
(`landing.html:1135-1152`): `mode_request → appendModeRequestBubble` (`:494-551`),
`decision_request → appendDecisionBubble` (`:557-633`),
`vault_action_pending → appendVaultRequestBubble` (`:428-492`);
`vault_action_resolved` → plain system line, every other kind (incl. `gate`,
`workflow_*`, `auto_bounce`) → generic `[kind] text` line (`:1147`). Vault and mode
are near-identical (Allow/Deny, same `.mode-req__*` classes, same
disable→POST→resolve flow); decision swaps the two buttons for a free-text reply
field and supersedes any prior bubble for the same `data.entity`
(`landing.html:557-565`). Copy-pasted approve/deny at `:435-491`, `:500-550`,
`:570-631` (bubbles) + `:1460-1500`, `:1582-1622` (bell renderRows). Also
copy-pasted three times: the attention-routing side-effects
(`HiveDrawer.announce` + `markUnread` + `scrollMessagesToBottom`, `:449-454`,
`:513-518`, `:582-587`, mechanism at `:976-989`). Bubbles are **live-push** (SSE);
bells are **pull-on-open** (poll only when clicked) — two delivery models for
overlapping data (a mode request could double-render during migration).

**Q3 (the web stack).** htmx + Jinja2 server-rendered fragments for the polled
regions; vanilla JS + SSE for the bubbles; **no client framework** on `/`
(`app.py:68-91`, `landing.html:12,277-1666`). See PC-1. The five htmx-polled
regions are innerHTML swaps of `/api/landing/*` fragments — hero 30s / vault 15s /
active 5s / idle 5s / dormant 30s — all via one `_build_view()` helper
(`landing.html:151-204`, `app.py:494-529`). "One lane component" is a
`_partials/needs_you.html` partial fed by a view-model `needs_you` list, htmx-polled
(match the 5s active/idle cadence), with one delegated action handler.

### Q4–Q8 — backing data + APIs (the rollup's inputs)

**Q4 (state + API per kind)** — see per-kind table below. Ownership: decision state
lives **on the entity** (`awaiting_decision`, `last_decision_question`, durable, no
store — `entity.py:226-249`), set only on the maestro→user `request_decision` path
(a lead→maestro `request_decision` is peer mail and sets no flag,
`message_dispatcher.py:443-489,490-509`) and cleared by `clear_awaiting_decision`
(`manager.py:214-244`, called from `dispatch.py:455-462`), which also sets a
maestro's `confirmed_with_user=True` (Ticket 019 side-effect). Mode + gate share one
table (`ModeRequestStore`, discriminated by a `kind` column — `mode_request_store.py:21-82`),
owned by `ApprovalHandler` (`approval_handler.py:82-114` mode, `:472-493` gate
notify). Vault rows live in `vault_store` (int PK + unique `idempotency_key`;
`009_vault_actions.sql`, `022_vault_actions_payment_fields.sql:6,16`), full payment
lifecycle in `ApprovalHandler.approve_vault_action` (`:116-378`, idempotent —
non-pending rows short-circuit at `:232-233`; only `role=='vault'` may request,
`:162`). Errored = scan `process_manager.entities` for `state==EntityState.ERROR`
(no store, no endpoint, PC-3).

**Q5 (keying — dedupe/resolve/remove).** Two schemes, cannot be unified:
mode/vault/gate are **row-id'd** (int PK; resolve by id) and decision is
**entity-keyed** and **one-deep / supersede-on-reask** (ADR 0024;
`app.py:327-351,353-365`, `landing.html:557-565`). Lane item needs a discriminated
key `(kind, id-or-entity)`. **Removal-on-answer:** poll-based re-derivation is the
reliable path — the SSE bubbles resolve purely client-side with **no server resolve
event** for decision or mode (only `vault_action_resolved` is fired);
`clear_awaiting_decision` has **no `_notify`** (`manager.py:214-225`), so an SSE
"clear" never arrives — a decision answered on device A does not clear on device B
until reload. 039 chose poll-only for exactly this reason ("SSE SET fires but CLEAR
fires nothing → an SSE badge would stick", `039/design.md:13`,
`039/research.md:61-69`, `message_dispatcher.py:472-477`). See Q10.

**Q6 (fields for entity/kind/prompt/action).** Payload shapes are heterogeneous:
mode/gate = `{requester, requested_mode, reason}`; vault =
`{requester, amount_cents, currency, recipient, reason}`; decision =
`{entity, question}`; errored = entity name + `EntityState.ERROR` only
(`view_model.py:287-317`, `landing.html:454-579`). The `prompt/summary` field:
decision → `last_decision_question`; mode → `reason`/`requested_mode`; vault →
`reason` + money detail; gate → n/a (dropped); **errored has no natural
prompt/summary or notification text** — synthesize it in the view-model (e.g. "loop
errored — reset to recover"). Normalize all onto one actor field `entity` in
`view_model.py` so the partial stays a dumb renderer (matches the existing
"view_model builds dicts, templates render" split).

**Q7 (action endpoints — one dispatcher or per-kind?).** Per-kind. All are
token-gated thin wrappers over `ProcessManager` methods (`app.py:319-455`),
unchanged by 051: mode `POST /api/mode-request/{id}/{approve|deny}`; gate
`POST /api/gate/{id}/{approve|deny}` (dropped with the surface — and web can't pass
the ask-option, always picks option 0, `app.py:395-403`,
`gate_coordinator.py:207-218`); vault `POST /api/vault-action/{id}/{approve|deny}`
(deny takes optional JSON `reason`; returns `{ok,id,status,executed_at,denial_reason}`,
`app.py:415-455`, `approval_handler.py:397-538`); decision
`POST /api/decision/{entity}/reply` (JSON `{reply}`, entity-keyed, routes through
the command message path). Four of five are row-id'd approve/deny; decision is a
reply-string. The lane needs a **per-kind action switch** (two-button vs
reply-input vs recover), routed by `data-*` attrs through one delegated listener —
not one uniform endpoint. Auth: bearer `HIVE_WEB_TOKEN` from
`localStorage['hive_web_token']`, same for every action (`app.py:175-176`,
`landing.html:282-313,456-490`) — no new auth work; `require_token` already guards
every endpoint the lane calls.

**Q8 (errored — state + action).** State = `EntityState.ERROR`, two producers, no
web surface (PC-3). **Action = `/reset <entity>`** via generic
`POST /api/command → CommandDispatcher` (`dispatch.py:108,736-753`). Critical
gotcha: **nothing in the normal message/send path clears `ERROR`** —
`send_to_entity` guards only on `is_parked_at_gate` and never transitions state
(`message_dispatcher.py:100-233`, `lifecycle_manager.py:329-373`); the scheduler
does **not** skip ERROR maestros (`scheduler.py:248-275`), so an errored entity
keeps being poked and burning quota while invisible as "dormant". Only `/reset`
(and `compact_entity`) raw-assign `state=IDLE` (`dispatch.py:736-753`,
`lifecycle_manager.py:582`; ERROR→IDLE is a legal transition, `entity.py:42`). So
the errored lane action **must** be `/reset`, not a plain reply — a reply would run
but the item would never leave the feed (un-dismissable). No new act-mechanic
needed, but new read-path view-model code. Note also: `health_check` (one of the two
ERROR setters) emits only an `entity.error` audit row, no notification, and runs
only on-demand via `/health` (`manager.py:733-737`) — so the errored feed can't rely
on a notification; it must poll entity state.

### Q9–Q10 — delivery mechanics

**Q9 (does 039 already roll these up?).** Partially, and it must be *extended*, not
reused. `_is_awaiting` folds `awaiting_decision` (029) + `is_parked_at_gate` (003)
into one **bool per maestro card** (`view_model.py:125-139`); `_awaiting_rollup` ORs
it across the `maestro.team` org-prefix tree (`:142-159,194`). It **deliberately
excludes** mode/vault ("stay on the bell", `:132`) and errored entirely, and is a
bool not a list. 051's `needs_you` is a **generalization/rewrite** of
`_awaiting_rollup` into a per-item list `{entity, kind, summary, action}`. The hero
builder already pulls `vault_store.pending('vault')` +
`mode_request_store.list_pending(default_maestro)` and computes `pending_total`/
`highest` (`view_model.py:287-308,356-374`) — reuse those store queries; both
kinds can come from one `list_pending(default_maestro, kind=None)` call split on the
`kind` field (`mode_request_store.py:57-82`) — but **CONFIRM IN CODE** the de-dup,
since the current mode endpoint omits `kind` and may already include gate rows. The
`#awaiting-empty` "Nothing needs you right now." line + `refreshAwaitingEmpty` /
`htmx:afterSwap` re-evaluation (`landing.html:194,890-912`) is the ready-made
calm-empty-state model. (Q14.)

**Q10 (rollup transport).** Recommendation from the sweep: **pull-side server rollup**
exposed as an htmx-polled partial (e.g. `/api/landing/needs-you` via the existing
`_build_view()` helper, `app.py:494-529`), poll interval matching the urgent regions
(active/idle 5s); **SSE demoted to a "re-poll now" nudge**, not the render source.
Rationale: (a) the SSE queue is bounded (maxsize 100), drop-oldest, best-effort — a
sleeping tab silently loses frames (`sse.py:24,30-62`), so a push-only lane
structurally loses items (the cold-open trap, PC-8); (b) one server rollup gives ONE
reseed path covering all sources, killing the vault-no-reseed gap; (c) keeps the
lane server-rendered Jinja, reusable verbatim by 052/053; (d) decouples the lane
from wire `kind` churn. **Do NOT add a new `needs_you` notification kind on the
wire** — the five `ALERT_KINDS` strings are a cross-surface contract (push switch,
Telegram suppression, email digest, tests) with no single indirection point beyond
`ALERT_KINDS` (`dispatcher.py:35-43`, `web_push.py:34-61`, `bridge.py:144-148`,
`email.py:88-90`, `test_web_push_channel.py:55-168`,
`test_telegram_alerts_toggle.py:50-66`). SSE frames are unnamed (`data:` only, `kind`
inside the JSON, `sse.py:65-75`) — filtering is client-side, so the lane's re-poll
trigger must whitelist the needs-you kinds. Single emit point is
`ProcessManager._notify` (`manager.py:741-752`) — no new emit path needed. Build the
rollup server-side from existing state; keep SSE kinds stable.

### Q11–Q13 — push + deep-link coupling

**Q11 (actionable-set alignment).** They do **not** match. `ALERT_KINDS =
{decision_request, mode_request, vault_action_pending, workflow_completed,
workflow_failed}` (`dispatcher.py:35-43`). The lane's five (decision, mode, vault,
gate, errored) overlap on 3; **gate and errored are absent from push** (a parked
gate — if one existed — and an errored loop produce no lock-screen ping); and
`ALERT_KINDS` includes two run-ended kinds that are **not** blocking asks. If 051
ever wants gate/errored to push, it must add both the `ALERT_KINDS` entry **and** a
matching `elif` in `web_push.py` — which has **no `else` branch**, so an unhandled
ALERT kind hits `UnboundLocalError` on `json.dumps`, swallowed silently by the
channel's bare `except` + the dispatcher's per-channel isolation
(`web_push.py:34,42-63,82-83`, `dispatcher.py:81-87`). Out of scope for 051 (push is
041) but flag the footgun.

**Q12 (deep-link re-pointing).** 048's push deep-link uses `/?reply=<entity>` (opens
chat pre-addressed) for needs-you kinds and `/?focus=<entity>&run=<run_id>` for
run-ended (`web_push.py:42-61`, `landing.html:655-680`, `service-worker.js:144-158`).
It targets the **chat drawer / card**, not the bells or bubbles this ticket deletes
— so no re-point is *forced* by 051. But it is entity-keyed, so lane items must
carry a stable `data-entity` anchor (mirror `[data-entity]` / `.is-focused`,
`landing.html:672-676`) so a tap converges on the same open-chat/focus handler. For
052/053 the deep-link must eventually resolve to a maestro **tab**, not just a
drawer — `?reply=<entity>` is the stable hook to thread through. (Also fix the
empty-entity bug, PC-9.)

**Q13 (gates on the web post-029).** Effectively legacy/no-op — dead end-to-end.
See PC-4. Note a *second* representation exists: `_is_awaiting` reads the in-memory
`is_parked_at_gate` predicate (`gate_coordinator.pending_request_id`,
`manager.py:360-373`), a **different source** than the `kind='gate'` DB rows the
(dead) gate bell reads. Both are inert under current tool policy. There is also no
`gate` branch in the SSE switch and `gate` is not in `ALERT_KINDS` — gates never
push or bubble (`landing.html:1138-1147`, `dispatcher.py:37-41`). 051 drops the
web-facing half; the runtime detector can stay as an inert safety net.

### Q14 — contract with 052/053

**Q14 (lane interface for hero + Work view).** Expose one view-model key:
`needs_you = {count: int, items: [{entity, kind, summary, action_endpoint, id-or-
entity, ...}]}`. 052 hero: calm/loud is a **pure function of `count == 0`**
(`count==0` → "✓ all clear · N loops running" using `hero.active_count`, else
loud); the derivation already has `hero.active_count/idle_count/dormant_count` +
`hero.mood` (`view_model.py:356-364`, `052/ticket.md:9-11`, `051/ticket.md:24-28`).
053 Work view: because every item carries `entity`, filter the same component to one
maestro via the org-prefix idiom `[i for i in items if i.entity startswith f"{tab}."]`
(reuse `_awaiting_rollup`'s prefix logic, `view_model.py:154-159`;
`_partials/idle.html:6` exposes the `/m:{name}` address; `053/ticket.md:8-14`). Each
item needs a `data-entity` anchor for tap-to-open. **Ownership of the empty string:**
039's `#awaiting-empty` "Nothing needs you right now." (`landing.html:194`) vs 052's
"✓ all clear" hero — the design must assign one owner (recommend 051 owns the lane
partial's calm state; 052 owns the hero headline). Three separate empty strings
(`#awaiting-empty`, two `.bell-popup__empty`) collapse to one
(`landing.html:194,1455-1457,1577-1579`, `landing.css:1469-1476`).

**Implementation constraint (survives htmx swaps).** If the lane is an htmx-polled
region (swapped every 5s), its action buttons **must** use delegated listeners on
`document`/`body` (like the existing `[data-cmd]` and `#awaiting-filter` handlers,
`landing.html:878-912`) — the current per-row `addEventListener` wiring in
`renderRows`/bubbles would be wiped on each poll. Route by
`data-item-type`/`data-item-id`/`data-action`. Single most important porting gotcha.

### Q15 — testing

Two harness patterns (`test_web_landing.py:18-26`,
`test_view_model_awaiting.py:29-51`): **(A)** endpoint tests via
`TestClient(create_app(process_manager=<MagicMock>, **stores))` with AsyncMock
facade methods + `monkeypatch` on `hive.web.auth.WEB_TOKEN` — for JSON shape/auth;
**(B)** view-model tests calling `build_landing_view_model(...)` directly + hand-built
Jinja `Environment(FileSystemLoader(TEMPLATES_DIR))` — for the `needs_you` key +
partial markup. New files: `tests/test_web_needs_you_endpoints.py` +
`tests/web/test_view_model_needs_you.py`.

- **Truthy-Mock pitfall:** a bare `MagicMock` PM makes `is_parked_at_gate` return a
  truthy Mock, `_is_awaiting` coerces with `bool()` and falsely flags every card
  awaiting (`test_view_model_workflow.py:82-89`, `test_view_model_awaiting.py:34-51`).
  Any new predicate the rollup consults (vault/mode pending, `EntityState.ERROR`)
  must get an explicit return on a hand-rolled `_FakePM` — never a bare MagicMock —
  or assertions silently pass.
- **Certain to break:** `TestGatePanel` (`test_web_dashboard.py:65-80`, asserts
  `gate-btn`/`gate-popup`/"Pending gates"/`/api/gates/pending` in HTML) — delete or
  rewrite to assert the lane. `test_web_gate_endpoints.py:53-79` bakes the
  dead-filter bug in (`list_pending.assert_awaited_once_with("otter", kind="gate")`)
  — update if gate listing changes. CACHE_VERSION pin (`test_web_ipad_polish.py:75-91`,
  currently `hive-v6`, asserts v5/v2 absent) — 051 changes CSS/JS → bump
  `service-worker.js` to `hive-v7` and update asserts; coordinate the bump across
  051/052/053 (all touch `src/hive/web`).
- **`test_view_model_awaiting.py` (15 tests):** pin the 039 `awaits`/`is-awaiting`
  per-card markup (`_macros.html:72,81`, `idle.html:4,8`) + the org-prefix rollup.
  Break if that markup is removed. Design fork: keep the card badge as a secondary
  indicator (tests survive) or fold into the lane (rewrite 15).
- **`test_web_landing.py:83-146`:** pins `approvals_count`/`vault.*` + the 10
  top-level view-model keys; `test_vault_pending_counted` asserts `approvals_count==1`.
  Lower-risk: **ADD** `needs_you` alongside the existing keys (the empty-keys
  presence test survives), then migrate the template.
- **Survive unchanged (keep the act path):** all four endpoint-test files pin only
  routes + JSON shapes, never bells/bubbles — `test_web_decision_endpoints.py`,
  `test_web_mode_request_endpoints.py`, `test_web_vault_endpoints.py`,
  `test_web_gate_endpoints.py` (gate POSTs survive even as the panel goes). This is
  the safe seam: keep every per-kind POST, land the lane as a read/aggregation
  surface only.
- **DB-backed aggregation test:** `vault_store`/`mode_request_store` fixtures
  (session-scoped pgvector testcontainer, no `integration` marker → run in the
  default suite, `conftest.py:84-177`) let a real-DB test seed a pending vault +
  mode row and assert both appear in the rollup.
- **No JS test harness exists** anywhere (`test_web_dashboard.py:37-47`) — all lane
  browser behaviour is **iPad-smoke-gated**; write server-side view-model + render +
  endpoint tests only.

### Q16 — out-of-scope check

Act path needs **no new mechanics** — every resolve endpoint (mode/vault/gate POST,
decision reply, `/reset` via `/api/command`) already exists and is reused unchanged
(`app.py:319-455`, `dispatch.py:108`). The read/rollup side is where the new code
lives. The one item that pushes near the scope edge is **errored** — see Scope check.

---

## Per-kind table

| kind | pending state | GET (list) | POST (act) | key |
|---|---|---|---|---|
| decision | `entity.awaiting_decision` + `last_decision_question` (on-entity, durable, no store; ADR 0024, `entity.py:226-249`) | `GET /api/decisions/pending` scans entities (`app.py:353-365`) | `POST /api/decision/{entity}/reply` `{reply}` (`app.py:327-351`) | **entity** (one-deep, supersede-on-reask) |
| mode | `mode_requests` rows, `kind='mode_request'` (`mode_request_store.py:21-82`; created `approval_handler.py:45-114`) | `GET /api/mode-requests/pending` = `list_pending(default_maestro)` (`app.py:319-324`) — **lists lead→otter rows only, PC-5** | `POST /api/mode-request/{id}/{approve\|deny}` (`app.py:367-385`) | **row id** (int) |
| vault | `vault_store` rows, int PK + `idempotency_key` (`009/022 migrations`; lifecycle `approval_handler.py:116-378`) | **NONE** — no web GET; only SSE bubble + vault-card count, PC-8 (`vault_store.pending("vault")` at `view_model.py:290`) | `POST /api/vault-action/{id}/{approve\|deny}` (deny takes `reason`) (`app.py:415-455`) | **row id** (int) |
| gate | `mode_requests` rows, `kind='gate'`, `approver='user'` — **but no rows producible, PC-4** (`gate_coordinator.py:88-101`) | `GET /api/gates/pending` = `list_pending('otter', kind='gate')` — **DEAD, always `[]`, PC-4** (`app.py:388-393`) | `POST /api/gate/{id}/{approve\|deny}` (`app.py:395-413`) — web can't pass ask-option (always 0) | row id (moot) |
| errored | `entity.state == EntityState.ERROR` (on-entity; 2 setters `manager.py:505-508,729-738`) — collapses to "dormant", PC-3 | **NONE** — no endpoint, no notification, no renderer | **`/reset <entity>`** via `POST /api/command → CommandDispatcher` (`dispatch.py:108,736-753`) — a plain reply does NOT clear ERROR (Q8) | **entity** |

---

## Existing PARTIAL aggregations (and why none is a unified `needs_you`)

- **`approvals_count`** (`view_model.py:287-297,357`) = `len(vault_pending) +
  len(mode_pending)`. Drives the bell badge. Excludes decision, gate, errored; the
  mode half is the lead→otter approver scope (PC-5); frozen at page load (PC-6); its
  count disagrees with its own popup (PC-6). A vault+mode subtotal, not the
  needs-you set.
- **`_awaiting_rollup` / `_is_awaiting`** (`view_model.py:125-159`) = per-card
  **bool** over decision + gate only; deliberately excludes mode/vault (`:132`) and
  errored. Powers 039's "Waiting on me" card filter (`landing.html:890-912`).
  Reusable as the decision feeder + org-prefix idiom, but it is a bool not a list —
  051 generalizes it.
- **`ALERT_KINDS`** (`dispatcher.py:35-43`) = push/Telegram-suppression set;
  decision+mode+vault + 2 run-ended kinds. Omits gate + errored. A wire-protocol
  contract, not the on-page actionable set.

**Plainly: no unified `needs_you` rollup, key, or endpoint exists.** Four disjoint
notions (badge / card-filter / bubbles / push) each define a different subset (PC-7).
051 builds the canonical set from scratch.

---

## Scope check — is each net-new piece in-spirit or out-of-scope?

| net-new piece | verdict | why |
|---|---|---|
| **Errored feed *source*** (scan `entities` for `EntityState.ERROR`) | **In scope — read-path inside the consolidation.** | Write-side (state being set) already exists (`manager.py:505-508,729-738`); 051 only adds a view-model derivation, no new *mechanic*. But flag: genuinely new view-model code with **no existing surface or test to lean on** (PC-3) — the largest single build in 051; the ticket's "consolidate 4 surfaces" framing undersells it. |
| **Errored *action* (`/reset`)** | **In scope — reuses existing API.** | `/reset <entity>` via `POST /api/command` already exists (`dispatch.py:736-753`); the lane just wires a button. **Constraint:** must be `/reset` (state-clearing), not a plain reply, or the item is un-dismissable (Q8). No new mechanic. |
| **Vault pending read path** | **In scope — bug-fix inside the consolidation.** | Closes a real reload-loss gap (PC-8). Since the lane is server-rendered, the rollup reads `vault_store.pending("vault")` straight into the view-model — **no new JSON endpoint strictly required** (a partial suffices). Pure read-path. |
| **Mode approver-scope fix (disjoint-set, PC-5)** | **In scope — bug-fix inside the consolidation.** | The bell and bubble showing non-overlapping sets is a live defect the unification must resolve. 051 picks ONE approver scope for the lane (and decides whether lead→maestro escalations belong in the *human's* lane). A store-query/scope decision, not a new mechanic. **CONFIRM IN CODE:** whether every `mode_requests.requester`/`vault` value maps to a live entity name, and the exact approver set the lane should query. |
| **Gate item / popup** | **Out — delete, don't build.** | Dead end-to-end (PC-4). Adding a gate lane item carries dead machinery forward. Recommend deleting the popup + `/api/gates/pending` + the `is_parked_at_gate` branch of `_is_awaiting`. |
| **`data['entity']` normalization + push deep-link repair (PC-9)** | **In-spirit drive-by; keep push work out.** | Normalizing the rollup onto `entity` is in scope (the lane needs one keying). Actually *changing* `web_push.py` / adding gate/errored to `ALERT_KINDS` is push-delivery work = **041's domain, out of scope** (ticket non-goal). Document the divergence; don't extend the push switch here. |
| **New wire `needs_you` notification kind** | **Out — do not add.** | The rollup is a server-side view over existing state; a new wire kind would break the cross-surface `ALERT_KINDS` contract (Q10). Keep SSE/notification kind strings stable. |
