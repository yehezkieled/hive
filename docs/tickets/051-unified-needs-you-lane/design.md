# 051 — Design (FINAL)

> Chosen approach for the unified "needs-you" lane. Every decision below is
> resolved explicitly against `research.md` (9-reader sweep) and confirmed in
> live `src/` (re-verified in this pass). Feeds `outline.md` → `plan.md`, and
> hands a mount contract to 052 (Stack home) and 053 (Work view).

---

## The shape in one line

A **server-side `needs_you` rollup** built in `view_model.py` from existing
state, rendered by **one Jinja partial** (`_partials/needs_you.html`) as an
**htmx-polled region** (5s), with **one delegated JS action handler** that does
a **small per-kind switch** over the **existing per-kind POST endpoints**. SSE
is demoted to a "re-poll now" nudge. No new approval mechanics, no new wire
notification kind, no React.

```
BEFORE (4 disjoint notions of "needs you")          AFTER (one canonical set)
──────────────────────────────────────────          ─────────────────────────
 bell badge   = vault + mode  (stale, PC-6)  ┐
 bell popup   = mode only     (PC-5)         │
 gate popup   = nothing       (dead, PC-4)   ├──▶  needs_you = {count, items[]}
 SSE bubbles  = mode+vault+decision (lossy)  │      one rollup → one partial
 card filter  = decision+gate bool (039)     │      → one delegated handler
 errored      = invisible ("dormant", PC-3)  ┘      → existing POST endpoints
```

---

## Data-flow of the rollup (ASCII)

```
  SOURCES (existing state, no new stores)                view_model.py
  ────────────────────────────────────                  ─────────────
  process_manager.entities ──┐
    e.awaiting_decision      │  scan: awaiting_decision → decision items
    e.last_decision_question │  scan: state==ERROR      → errored items
    e.state==EntityState.ERROR ─┘        │
                                         │
  mode_request_store ────────────────────┤  UNION of two approver scopes
    kind='mode_request' rows             │  (D2): list_pending(default_maestro)
    (kind='gate' rows: DROPPED, PC-4)    │  + list_pending('user'), each split
                                         │  on row['kind'] → mode items
                                         │  (gate rows filtered OUT)
                                         │
  vault_store ───────────────────────────┤  pending("vault") → vault items
    pending("vault") rows                │  (NEW read into the lane, PC-8)
                                         ▼
                              build_needs_you(...)  ← new pure builder
                                         │  normalize every item onto ONE
                                         │  actor field `entity` (full dotted
                                         │  address) + a discriminated key
                                         │  (kind, id-or-entity) + summary
                                         │  + action descriptor, pre-sorted
                                         │  by priority server-side
                                         ▼
                        view["needs_you"] = {count, items[], loud}
                                         │
        ┌────────────────────────────────┼───────────────────────────────┐
        ▼                                ▼                                ▼
  landing.html                    052 hero (Stack home)            053 Work view
  <section hx-get=                 count==0 → calm "✓ all clear"   filter items by
   /api/landing/needs-you          count>0  → loud hero            org-prefix idiom
   hx-trigger="every 5s,           reuses view["needs_you"]        (D9); one tab =
    needs-you:refresh from:body">   (052 owns the headline copy)   one maestro org
   {% include _partials/needs_you.html %}
        │
        ▼  each item: HEADER = open-tab target (D9); action controls = answer-in-place
   delegated click handler on document
   switch(item.kind) on [data-action] only (NOT header taps):
     decision → POST /api/decision/{entity}/reply {reply}
     mode     → POST /api/mode-request/{id}/{approve|deny}
     vault    → POST /api/vault-action/{id}/{approve|deny} [deny: {reason}]
     errored  → POST /api/command {command:"/reset <entity>"}  (confirm affordance)
        │
        ▼  after any POST resolves → htmx re-poll (trigger 'needs-you:refresh' on body)
   next 5s poll re-derives the list from source-of-truth state → item drops.
   The nudge ALSO re-triggers the active/idle regions so the 039 card badge
   heals on the same tick (D8 — no cross-surface flicker).
```

The self-heal is the point: because the list is **re-derived from live state
every poll**, an item answered on device A (or resolved cross-surface — a
decision answered on Telegram, a vault approved by another tab) simply stops
appearing on the next poll on device B. No client-side bookkeeping, no
server "resolve" event needed. This is the 039 precedent (`039/design.md:13`).

---

## Component sketch

```
 landing.html  (persistent wrappers; innerHTML-swapped regions)
 ┌──────────────────────────────────────────────────────────────┐
 │  <section class="needs-you"                                   │
 │           hx-get="/api/landing/needs-you"                     │
 │           hx-trigger="every 5s, needs-you:refresh from:body"  │
 │           hx-swap="innerHTML">                                │
 │     {% include "_partials/needs_you.html" %}   ◀── the lane   │
 │  </section>                                                   │
 └──────────────────────────────────────────────────────────────┘

 _partials/needs_you.html
 ┌──────────────────────────────────────────────────────────────┐
 │ {% if view.needs_you.count == 0 %}                            │
 │   <p class="needs-you__empty">✓ all clear</p>   ◀── D10       │
 │ {% else %}                                                    │
 │   {% for item in view.needs_you.items %}                      │
 │     {{ needs_you_item(item) }}   ◀── ONE macro, per-kind body │
 │   {% endfor %}                                                │
 │ {% endif %}                                                   │
 └──────────────────────────────────────────────────────────────┘

 macro needs_you_item(item)  (src/hive/web/templates/_macros.html)
   <article class="nyi" data-nyi-entity="{{ item.entity }}"    ◀ distinct anchor (D7)
            data-kind="{{ item.kind }}" data-id="{{ item.id }}">
     <header class="nyi__head">  ◀ HEADER = open-tab target (052 binds; D9)
        entity · kind · summary
     </header>
     <div class="nyi__actions">  ◀ answer-in-place; [data-action] handled here
       {% if item.action == 'reply' %}   ◀ decision
          <input class="nyi__reply"> <button data-action="reply">
       {% elif item.action == 'approve_deny' %}   ◀ mode, vault
          <button data-action="approve"> <button data-action="deny">
       {% elif item.action == 'reset' %}   ◀ errored (confirm affordance, D3)
          <button class="nyi__reset" data-action="reset">
       {% endif %}
     </div>
   </article>

 delegated action handler — shared JS module (D9), NOT buried in landing body
   listens on document for click on [data-action] inside .nyi__actions
   → reads data-kind/data-id/data-nyi-entity → per-kind fetch()
   → on 2xx, htmx.trigger(body,'needs-you:refresh')
   → MUST NOT swallow header taps (header is 052's open-tab target)
```

Four hand-rolled approve/deny rows + three bubble builders + two bell popups +
three empty strings collapse to **one macro + one handler + one empty string**
(PC-2, Q14).

---

## Decisions

### D1 — Transport: server-side rollup on the htmx poll (NOT SSE-driven feed) ✓ CHOSEN

**Decision:** build `needs_you` server-side in `view_model.py`; expose it as an
htmx-polled partial `GET /api/landing/needs-you` via the existing `_build_view()`
factory; poll at **5s** (matching the urgent active/idle regions). **SSE is
demoted to a "re-poll now" nudge** — on a needs-you-kind frame the client fires
`needs-you:refresh` on `<body>`; it is not the render source.

**Why (the constraint that eliminates SSE-as-source):**

```
  SSE-as-render-source                    poll-rollup-as-source (CHOSEN)
  ────────────────────                    ──────────────────────────────
  SET fires a frame ✓                     every 5s: re-derive from live state
  CLEAR fires NOTHING ✗                   answered item just stops appearing
   (clear_awaiting_decision has no        (no clear event needed — self-heal)
    _notify, manager.py:214-225;
    only vault_action_resolved fires)
  queue maxsize 100, drop-oldest,         one reseed path covers ALL sources
  best-effort → sleeping iPad tab          → kills the vault cold-open trap (PC-8)
  silently loses frames (sse.py:24-62)
  push-only lane structurally loses       server-rendered Jinja → reusable
  items on reload (the cold-open trap)     verbatim by 052/053 (D9)
```

Four independent facts force the poll: (a) there is **no CLEAR event** for
decision or mode, so an SSE-badged item would stick until reload
(`manager.py:214-225`, `message_dispatcher.py:472-477`); (b) the SSE queue is
lossy on a sleeping tab (`sse.py:24-62`); (c) vault has **no reseed path at all**
today, so a reload loses it (PC-8) — a server rollup fixes that for free; (d) a
server-rendered partial is what 052/053 need to embed. This is exactly why 039
chose poll-only (`039/research.md:61-69`).

SSE stays useful as a **latency reducer**: the client already has the
`es.onmessage` switch (`landing.html:1135`) — we replace the three bubble
builders with a one-line "whitelist kind → trigger refresh". The wire `kind`
strings never change (D6).

---

### D2 — Kind scope: decision + mode + vault + errored. Gate DROPPED. ✓ CHOSEN

**Canonical needs-you set = `{decision, mode, vault, errored}`.**

| kind | verdict | reason |
|---|---|---|
| **decision** | **include** | Live, entity-keyed, durable on the entity (`entity.py:226-249`). The core supervise-by-exception item. |
| **mode** | **include** | Live `mode_requests` rows. **Union both approver scopes** — see below. |
| **vault** | **include** | Live payment rows. Adds the missing reseed read path (PC-8). |
| **errored** | **include (net-new)** | `EntityState.ERROR` is set (`manager.py:505-508,729-738`) but collapses to "dormant" in the UI (PC-3). A **first-class net-new build item** — see the callout. |
| **gate** | **DROP — delete, don't migrate** | Dead end-to-end (PC-4): both emitting tools (`ExitPlanMode`/`AskUserQuestion`) are bare-name-denied post-029; the permission gate never fires under bypass (ADR 0005); and `GET /api/gates/pending` is already broken (approver never intersects, always returns `[]`). Migrating it carries dead machinery forward. |

**Gate cleanup (part of "collapse", not new work):** delete the `#gate-btn`
bell + `initGatePanel` + `GET /api/gates/pending`, and drop the
`is_parked_at_gate` branch from `_is_awaiting` (`view_model.py:136-139`). **Keep**
the `POST /api/gate/{id}/{approve|deny}` endpoints and the in-memory
`is_parked_at_gate` runtime detector as an inert safety net — only the
web-facing read half is dead (PC-4, Q13). Keeping the POSTs also keeps
`test_web_gate_endpoints.py`'s act-path tests valid.

**Mode approver scope — RESOLVED (was: PC-5 disjoint-set bug).** Confirmed in
code, `_approver_for` (`approval_handler.py:45-56`) sends mode requests to
**two disjoint approvers**:

```
  mode request origin            approver value        surfaced today by
  ───────────────────            ──────────────        ─────────────────
  maestro's OWN request          "user"                the SSE bubble only
   (entity.role == "maestro")
  lead → parent maestro          entity.maestro_name   the bell only
   (lead escalation)              (e.g. "otter")       (list_pending('otter'))
```

These sets **never overlap** — which is precisely why the pre-051 bell and
bubble showed different things (PC-5). A single coherent lane MUST show both:

> **CHOSEN (decided here, not deferred): the lane UNIONS both scopes.** It reads
> `list_pending(default_maestro)` **and** `list_pending("user")`, filters each on
> `row['kind'] == 'mode_request'` (drops `gate`), and dedupes by `id`. Rationale:
> a lead→maestro escalation is a decision the human makes on the maestro's
> behalf; a maestro's own `approver='user'` request is a decision the human makes
> directly. Both belong in the human's lane, and omitting either re-creates the
> disjoint-set bug this ticket exists to kill. `list_pending(approver, kind)`
> supports both calls (`mode_request_store.py:57`). This is the one scope call
> that defines the lane's correctness, so it is nailed by a **DB-backed
> aggregation test** that seeds *both* a lead→maestro and a maestro-own mode row
> and asserts both appear.

`requester` is always a **live entity name** — `request_mode_change` does
`self._mgr._entities.get(requester)` and raises on a miss
(`approval_handler.py:78-80`), so a lead's requester is its full dotted address
(`otter.backend`), never a bare team. The rollup carries that value verbatim
into `item.entity`, guaranteeing the 053 org-prefix filter (D9) matches.

---

### D3 — Net-new pieces: all four IN SCOPE, because none adds an approval *mechanic* ✓

The non-goal is *"new approval **types** / new **mechanics**"*. Each net-new
piece here is **read-path or reuse**, not a new act mechanic:

| net-new piece | in scope? | why it does not violate "no new mechanics" |
|---|---|---|
| **errored feed source** | **yes** | Pure view-model derivation over `EntityState.ERROR` — the state is already set by two existing producers. No new store, endpoint, or write path. |
| **errored action** | **yes** | Reuses the **existing** `/reset <entity>` via `POST /api/command` (`dispatch.py:736-753`). **Hard constraint:** must be `/reset` (state-clearing) — `_execute_reset` sets `entity.state = EntityState.IDLE` (`dispatch.py:750`), the *only* state-clearing path; nothing on the message path clears `ERROR` (`message_dispatcher.py:100-233`), so a plain reply would run but the item would never leave the feed (un-dismissable, Q8). **Guard the UX:** `/reset` kills-and-respawns the session — a heavier, destructive action than approve/deny/reply — so the button gets a **confirm affordance** and is visually distinguished (`.nyi__reset`, not styled as approve). `/reset` is safe on an already-IDLE or mid-turn entity (it kills + clears + sets IDLE unconditionally), so a transient health-check flip that briefly shows/hides the button cannot corrupt state. |
| **vault pending read path** | **yes** | Bug-fix inside the consolidation. Since the lane is server-rendered, the rollup reads `vault_store.pending("vault")` straight into the view-model — **no new JSON endpoint needed**, the partial suffices (PC-8). |
| **mode approver-scope fix** | **yes** | Unioning the two disjoint approver scopes (D2) is a live defect the unification *must* resolve to have one coherent list. A store-query decision, not a new mechanic. |

**errored — first-class build item (net-new, biggest single piece).** Errored
is the one genuine build stretch: it has **zero web surface today** (PC-3) and
shares **no data path** with decision/mode/vault. It is defensible in 051 (write
side already set by two producers; action reuses `/reset`), but it must be
carried in `outline.md` / `plan.md` as its **own line item with its own budget
and its own view-model + synthesized-summary tests** — not folded into
"consolidate the 4 surfaces". **De-scope lever:** if the window tightens, errored
is the cleanest split to a follow-up ticket, because it shares no data path with
the other three. Name that explicitly as the sprint's designated 051 shrink.

**Deferred (out of 051):** see the **Deferred / out of scope** section below.

---

### D4 — Item model + keying: one uniform item carrying both key schemes ✓

There are **two irreconcilable key schemes** (Q5): decision + errored are
**entity-keyed** (supersede-on-reask, one-deep per entity, ADR 0024); mode +
vault are **row-id'd** (int PK). We don't unify the *values* — we carry a
**discriminated key** on one uniform item:

```python
# built in view_model.py; the partial is a dumb renderer
{
    "kind":     "decision" | "mode" | "vault" | "errored",
    "entity":   "otter.backend",          # ONE actor field, FULL dotted address (PC-9)
    "id":       int | None,               # row PK for mode/vault; None otherwise
    "key":      "decision:otter.backend"  # discriminated: f"{kind}:{id or entity}"
                | "mode:42" | "vault:7" | "errored:otter",
    "summary":  "loop errored — reset to recover",  # synthesized per kind (Q6)
    "action":   "reply" | "approve_deny" | "reset",  # drives which body renders
    "priority": "P0" | "P1" | "P2",       # server pre-sorts by this; see D9 contract
}
```

**Per-kind summary (normalized in the view-model, Q6):**
- decision → `last_decision_question`
- mode → `f"{requester} → {requested_mode}"` (+ `reason`)
- vault → `reason` + money detail (`amount_cents`/`currency`/`recipient`)
- errored → **synthesized** (no natural text exists): `"loop errored — reset to recover"`

**`entity` is always the full dotted address or the maestro name — never a bare
team.** Decision/errored read the live entity's `.name`; mode reads `requester`
(a live entity key, D2); vault reads its `requester` the same way. This is what
lets the 053 org-prefix filter (D9) match every item to exactly one maestro tab.

**How each renders its inline action** — via `item.action` (see component
sketch): `reply` → free-text input + send; `approve_deny` → two buttons;
`reset` → one recover button (with confirm, D3).

**Dedupe / resolve:** none needed at write time — the list is re-derived every
poll from source-of-truth state, so a resolved item is simply absent next poll
(D1 self-heal). The mode union dedupes by `id` (D2). The `key` exists for
**stable DOM identity across swaps** (so an input mid-type isn't clobbered) and
as the deep-link anchor (D7), not for server-side dedupe. Decision's
supersede-on-reask is already handled upstream (one `awaiting_decision` flag per
entity → at most one decision item per entity).

---

### D5 — Action model: keep the EXISTING per-kind POST endpoints ✓ CONFIRMED (challenge rejected)

**Keep all four per-kind routes; the lane does a small per-kind switch in one
delegated handler. Do NOT build a unified action endpoint.**

```
  UNIFIED endpoint (rejected)              PER-KIND switch (CHOSEN)
  ───────────────────────────              ────────────────────────
  POST /api/needs-you/{key}/act            decision → POST /api/decision/{entity}/reply
   → server re-dispatches by kind          mode     → POST /api/mode-request/{id}/{approve|deny}
   → breaks 4 pinned endpoint test files   vault    → POST /api/vault-action/{id}/{approve|deny}
   → 048 push deep-link / any external      errored  → POST /api/command {"/reset <entity>"}
     caller must be re-pointed
   → NEW act mechanic = non-goal violation  all token-gated, all already exist
```

Three reasons the per-kind switch wins, from the sweep:
1. **Tests depend on the routes.** `test_web_decision_endpoints.py`,
   `test_web_mode_request_endpoints.py`, `test_web_vault_endpoints.py`,
   `test_web_gate_endpoints.py` pin routes + JSON shapes and **survive
   unchanged** if we keep them (Q15). A unified endpoint rewrites all four.
2. **A new act endpoint is a new mechanic = the non-goal.** The whole point is
   "backed by existing APIs, no new mechanics" (ticket Acceptance).
3. **The action shapes are genuinely different** (Q7): two-button vs
   reply-string vs `/reset` command — a unified endpoint would just re-fan-out
   internally, adding a layer for nothing.

**Auth:** every route the lane calls is token-gated. The **server-side** guard is
`Depends(require_token)` on each route (`app.py:175`, `320`, `354`, `370`, `380`,
`389`, etc.); the **client-side** bearer read (`localStorage['hive_web_token']`)
lives in `landing.html`. Both facts hold — no new auth work.

**The one hard porting gotcha (Q14 "single most important"):** because the lane
is swapped every 5s, per-row `addEventListener` (as `renderRows`/bubbles do
today) would be **wiped on each poll**. The handler **must** be a single
delegated listener on `document`/`body`, routing by
`data-kind`/`data-id`/`data-action` — mirroring the existing `[data-cmd]` and
`#awaiting-filter` delegated handlers (`landing.html:878-912`). See D9 for where
that listener must physically live so it survives the 052 rewrite.

---

### D6 — SSE / notification kind stability: no renames ✓

**The lane consolidates the PULL side only. Zero wire-`kind` changes.**

`ALERT_KINDS = {decision_request, mode_request, vault_action_pending,
workflow_completed, workflow_failed}` is a **cross-surface contract**: the Web
Push switch, Telegram suppression, email digest, and their tests all consume
these exact strings (`dispatcher.py:35-43`, `web_push.py:34-61`, `bridge.py:144-148`,
`email.py:88-90`, `test_web_push_channel.py`, `test_telegram_alerts_toggle.py`).

Consequences for 051:
- **No new `needs_you` wire kind** (D1/D3). The rollup is a server-side *view*,
  not a new event.
- **No `ALERT_KINDS` edit.** `gate`/`errored` staying absent from push is a known
  gap (Q11) — adding them is 041's job, and `web_push.py` has no `else` branch so
  an unhandled ALERT kind would fail silently (Q11 footgun). Flagged, not fixed here.
- SSE frames are unnamed (`data:` only, `kind` inside JSON, `sse.py:65-75`), so
  the client-side re-poll trigger **whitelists** the needs-you kinds. Adding a
  kind to that client whitelist is a pull-side cosmetic, not a wire change.

---

### D7 — Deep-link (048) preservation ✓ — with an explicit anchor split

**The lane becomes the cold-open reseed for `?reply=<entity>`; `?focus=` keeps
targeting the 039 card, via a distinct lane anchor.**

048's push deep-link opens `/?reply=<entity>` (pre-address the chat) for
needs-you kinds and `/?focus=<entity>&run=<run_id>` for run-ended. Those URL
strings are **constructed in `web_push.py:45-61`**; the service worker
(`service-worker.js:144-158`) is the **click→focus forwarder** — it reads
`event.notification.data.url` and postMessages `hive-focus`, forwarding whatever
URL it is handed. The `?reply=`/`?focus=` targeting logic runs in
`window.hiveDeepLink` (`landing.html:655-687`).

Two obligations, and one collision to resolve:

1. **Cold-open reseed (strict improvement).** Today `loadPendingDecisions`
   reseeds decisions only; vault is lost on reload (PC-8). The `needs_you`
   partial renders on first page load (server-side), so **the lane IS the
   reseed** for every kind.

2. **`?reply=<entity>` convergence.** `hiveDeepLink` pre-addresses the chat input
   with `/m:<entity>` (`landing.html:660-668`) — it does **not** query the DOM by
   `data-entity` for `reply`, so the lane's anchor attribute is irrelevant to
   `reply`. Nothing to re-point; the lane just guarantees the item is present to
   act on after a cold open.

3. **`?focus=<entity>` anchor collision — RESOLVED (was a critical undesigned
   collision).** `hiveDeepLink`'s `focus` branch does
   `document.querySelector('[data-entity="<sel>"]')` (`landing.html:672`), which
   returns the **first** DOM match. The 039 `maestro_card` already carries
   `data-entity="{{ m.name }}"` (`_macros.html:72`). If lane items *also* used
   `data-entity`, and the lane renders above the cards (it is the hero, so it
   will), `?focus` would silently retarget from the card to the lane item.

   **Decision: keep `?focus` on the card.** Lane items use a **distinct**
   attribute `data-nyi-entity` (D4/component sketch), NOT `data-entity`. The
   `querySelector('[data-entity="…"]')` in `hiveDeepLink` is left unchanged and
   keeps hitting the 039 card. Rationale: `?focus` is the *run-ended* deep-link
   (its target is "show me the entity's card/run status"), a read-oriented jump —
   the card is the right destination, not an actionable lane row. This keeps the
   two anchors non-colliding and requires **no change to `web_push.py` or the
   service worker**.

4. **Drive-by (rollup-local only):** normalize mode/vault onto their live entity
   name inside the rollup's own field mapping so the item always has an `entity`
   (PC-9) — but only inside `build_needs_you`; we do **not** change
   `web_push.py`. The lane reads its own view-model fields; the push-side deep-link
   repair for mode/vault stays 041's job (documented divergence, not fixed here).

---

### D8 — Reconciliation with 039: the lane REPLACES `_awaiting_rollup`; card badge coexists (secondary) ✓

039 gave us `_is_awaiting` (per-entity bool over decision + gate) and
`_awaiting_rollup` (OR'd across the `maestro.` org-prefix tree,
`view_model.py:125-159`) — a **bool**, decision+gate only, deliberately excluding
mode/vault (`:132`).

**Chosen relationship:**
- **`needs_you` is a generalization/rewrite of `_awaiting_rollup`** — from a
  per-card **bool** to a per-item **list** `{entity, kind, summary, action}`,
  and from decision+gate to decision+mode+vault+errored. 051 **explicitly
  overturns** the documented 039 split at `view_model.py:132` ("mode/vault stay
  on the bell") — that split is what created the four disjoint notions (PC-7).
- **The 039 per-card "awaits" badge + `#awaiting-filter` chip COEXIST** as a
  secondary indicator, reading the same `awaiting_decision` state so they stay
  consistent.

**Impact on 039 tests — corrected (was "15 tests, gate branch not pinned"):**
`test_view_model_awaiting.py` has **11** test functions (`:74-153`), no
parametrization. Ten survive unchanged (we keep the card badge + org-prefix
rollup). **The gate branch IS pinned:**
`test_gate_parked_sets_flag_without_decision` (`:80-83`) builds
`_FakePM([dev], gated={"dev"})` with `awaiting=False` and asserts
`view["active"][0]["awaiting_you"] is True` — that can only pass because
`_is_awaiting` consults `is_parked_at_gate`. **Dropping the gate branch (D2) WILL
break this one test**, so it must be **deleted or rewritten** as part of this
change — it is NOT untouched. The other 10 survive.

`TestGatePanel` (`test_web_dashboard.py:65-80`) and the gate GET-filter test are
**deliberately deleted/rewritten** (they pin the dead surface, Q15).

**Timing lockstep (resolve the residual two-surface flicker):** the lane polls at
5s and re-polls on the SSE nudge; the 039 card badge lives in the active/idle
regions, which poll on their **own** independent `every 5s` htmx timer
(`landing.html:179,189`). After an item is answered, the lane heals on the
nudge-triggered re-poll while the card badge could linger until its own next tick
— a brief visible disagreement, the exact "two surfaces disagree" class 051 kills.
**Fix:** add `needs-you:refresh from:body` to the active/idle regions'
`hx-trigger` too, so both heal on the same tick (one line per region).

**Empty-string ownership (Q14):** three empty strings collapse to one. The 039
`#awaiting-empty` "Nothing needs you right now." (`landing.html:194`) and the two
`.bell-popup__empty` strings (`:1456,:1578`) are removed; **051 owns the lane's
calm state** ("✓ all clear", D10); **052 owns the hero headline** (D9/D10).

---

### D9 — 052/053 contract (the mount interface + view-model fields) ✓

**View-model fields (the data contract):**

```python
view["needs_you"] = {
    "count": int,                # len(items) — the loud/calm trigger; COMPLETE set (D2 union)
    "loud":  bool,               # count > 0  (convenience; 052 may derive its own)
    "items": [ {kind, entity, id, key, summary, action, priority}, ... ],
                                 # PRE-SORTED by priority server-side (see below)
}
```

**`priority` contract (was: exposed-but-undefined).** Items are **pre-sorted by
priority server-side** (P0 → P1 → P2; errored/decision high, mode/vault below).
**Consumers must NOT re-sort or re-derive severity** — that would re-fragment the
very ordering 051 centralizes. The 052 hero remains binary (loud iff
`count > 0`); it may *label* itself with the highest item's priority but does not
change the order. 053 preserves list order within its filter. This closes the
mini disjoint-set risk of leaving the field open to per-consumer interpretation.

**Mount interface (the component contract):**

| surface | how it mounts | consumes |
|---|---|---|
| **landing.html (this ticket)** | `<section hx-get="/api/landing/needs-you" hx-trigger="every 5s, needs-you:refresh from:body">` including `_partials/needs_you.html` | full `view.needs_you` |
| **052 Stack home hero** | `{% include "_partials/needs_you.html" %}` inside the hero region; calm/loud is a **pure function of `count == 0`** — **052 owns the headline copy** ("N loops running" etc.); 051 supplies only the bare calm line "✓ all clear" | `view.needs_you.count`, `view.hero.active_count` (both already built) |
| **053 Work view tab** | same partial, filtered to one maestro via the org-prefix idiom `[i for i in items if i.entity.startswith(f"{tab}.") or i.entity == tab]` (reuse `_awaiting_rollup`'s prefix logic, `view_model.py:154-159`) | `view.needs_you.items` + the tab's maestro name |
| **shared action handler** | included via a **shared JS module** (see below), NOT inline in `landing.html`'s body; must be present on any page that mounts the partial | the partial's `[data-action]` controls |

**Navigation vs answer-in-place — the two-target item (was: critical, 052 could
not open a tab).** 052's acceptance requires tapping a needs-you item to
**open/focus its Work-view tab**, but 051's item only exposes inline actions. To
serve both without 052 reaching inside 051's partial:
- The item **HEADER** (`.nyi__head`, carrying `data-nyi-entity`) is the
  **open-tab target**. 052/053 bind a click on the header that routes to the
  maestro's tab — reusing the existing `/m:<entity>` convergence that
  `hiveDeepLink` already implements (`landing.html:660-668`).
- The item **action controls** (`.nyi__actions`: input/buttons) are the
  **answer-in-place** target.
- The delegated `[data-action]` handler is **scoped to `.nyi__actions`** and
  **MUST NOT swallow header taps**. This lets 052 add navigation by binding the
  header only, without touching 051's action handler — the partial stays reusable
  verbatim.

**Where the shared action handler lives (was: warning, 052 rewrites landing).**
052 replaces today's fleet-monitor landing, so a handler buried in
`landing.html`'s body `<script>` would silently die on the new home (buttons
render, clicks do nothing). **Decision: the delegated `[data-action]` listener
lives in a small shared JS module** (e.g. `src/hive/web/static/needs_you.js`, or
a partial-scoped `<script>` both templates include), attached once on `document`
at a well-known init point. Both `landing.html` and the 052 Stack home include
it. This is the third mount-table row above — the handler is not ambient; its
location is pinned so it survives the 052 rewrite.

Every item carrying `entity` (D4, full dotted address) is what makes the 053
per-tab filter and the 052 hero count fall out of the same list — one source,
three views. The 053 filter is guaranteed to match because `entity` is a live
entity key (D2), and the DB-backed aggregation test asserts a seeded lead→maestro
mode row lands under the right maestro's tab filter (not merely "appears").

---

### D10 — Empty state: the calm "all clear" ✓

`count == 0` → the lane renders **one** calm line: `✓ all clear`. This replaces
the three separate empty strings (`#awaiting-empty` + two `.bell-popup__empty`,
`landing.html:194,1456,1578`) with one owner (051, per D8). The 039
`refreshAwaitingEmpty` / `htmx:afterSwap` re-evaluation machinery
(`landing.html:890-912`) is no longer needed for the lane — the partial renders
calm-or-loud server-side on every poll, so there's no client toggle to keep in
sync.

**Copy ownership:** 051 owns only the bare lane line ("✓ all clear"). 052 layers
its own hero headline on top of the count (e.g. "✓ all clear · N loops running")
— that string is **052's**, not written in 051.

---

## Files that change

**New:**
- `src/hive/web/templates/_partials/needs_you.html` — the lane partial (empty
  state + item loop).
- `src/hive/web/static/needs_you.js` — the single delegated `[data-action]`
  listener (D9), included by `landing.html` and later by the 052 Stack home so it
  survives the 052 rewrite.
- `tests/web/test_view_model_needs_you.py` — view-model `needs_you` key +
  per-kind item shape + `entity`-is-full-dotted-address + org-prefix filter
  (harness B: `build_landing_view_model` + Jinja `Environment`; use a hand-rolled
  `_FakePM`, **never a bare MagicMock** — the truthy-Mock pitfall falsely flags
  every predicate, Q15).
- `tests/test_web_needs_you_endpoints.py` — `GET /api/landing/needs-you` renders;
  auth; **DB-backed aggregation** seeding a real pending vault row **and both a
  lead→maestro AND a maestro-own mode row**, asserting all appear and that the
  lead→maestro row lands under the right maestro's org-prefix filter (D2/D9;
  session-scoped pgvector container, default suite, Q15).

**Modified:**
- `src/hive/web/view_model.py` — add `build_needs_you(...)` builder (scans
  `entities` for `awaiting_decision` + `EntityState.ERROR`; reads
  `mode_request_store.list_pending(default_maestro)` **and** `list_pending('user')`,
  each split on `kind` and deduped by `id`; reads `vault_store.pending("vault")`),
  normalize onto the full-dotted `entity`, pre-sort by priority, return
  `view["needs_you"]`; drop the `gate` branch of `_is_awaiting`.
- `src/hive/web/app.py` — add `GET /api/landing/needs-you` via the view factory;
  **delete** `GET /api/gates/pending` (`api_gates_pending`, `:389`). Keep all POST
  act routes (incl. gate POSTs).
- `src/hive/web/templates/landing.html` — add the `needs-you` polled region;
  add `needs-you:refresh from:body` to the active/idle regions' `hx-trigger`
  (D8 lockstep); **delete** `#bell-btn` popup + `#gate-btn` popup + `initBellPanel`
  + `initGatePanel` + the three SSE bubble builders (`appendVaultRequestBubble`/
  `appendModeRequestBubble`/`appendDecisionBubble`) + the three empty strings +
  the `#awaiting-empty` machinery; replace the `es.onmessage` bubble dispatch
  (`:1135`) with a needs-you-kind → re-poll nudge; include `needs_you.js`.
- `src/hive/web/templates/_macros.html` — add the `needs_you_item` macro
  (per-kind action body; header = open-tab target, `data-nyi-entity` anchor).
  (Existing landmarks in this file: `state_dot` :31, `maestro_card` :71, the
  awaiting badge markup :72/:81.)
- `src/hive/web/static/landing.css` — lane styles; **remove the dead `.bell*`
  rules** (`:218-232`, `:1587`, `:1657`, `:1692`, `:1668`); wire the existing
  `.state-dot--error` (`:987`) to errored lane items. (Note: `.bell-popup__*` /
  `.gate-*` exist only as inline JS class strings in `landing.html`, not as CSS
  selectors — nothing to delete for those in the stylesheet.)
- `src/hive/web/static/service-worker.js` — bump `CACHE_VERSION` to `hive-v7`
  (CSS/JS change). **051 OWNS the `hive-v7` bump; 052/053 rebase onto it — they
  must NOT each re-bump** (avoids the triple-bump rebase churn the sprint risks
  call out).

**Tests to delete/rewrite (pin the dead surface):**
- `test_view_model_awaiting.py::test_gate_parked_sets_flag_without_decision`
  (`:80`) — **delete or rewrite** (it pins the gate branch we drop, D8). The other
  10 tests in the file survive.
- `test_web_dashboard.py::TestGatePanel` (`:65-80`) — delete (asserts
  `gate-btn`/"Pending gates"/`/api/gates/pending`).
- `test_web_gate_endpoints.py` GET-filter test — remove the dead-filter assertion;
  keep the POST act tests.
- `test_web_landing.py` — **ADD** `needs_you` alongside existing keys; the
  `approvals_count` bell badge is removed, so update `test_vault_pending_counted`
  to assert the lane count instead.
- `test_web_ipad_polish.py` CACHE_VERSION pin — update to `hive-v7`.

---

## Alternatives considered

- **SSE-driven push feed (rejected, D1).** No CLEAR event → items stick until
  reload; lossy queue on a sleeping iPad tab; no cold-open reseed for vault; not
  server-renderable for 052/053. Poll-rollup dominates on all four.
- **Unified `POST /api/needs-you/{key}/act` endpoint (rejected, D5).** A new act
  mechanic = the explicit non-goal; would rewrite four pinned endpoint test files
  and re-point the 048 push deep-link, for a layer that just re-fans-out by kind.
- **Migrate `gate` into the lane (rejected, D2).** Gate is dead end-to-end post-029
  (both emitting tools bare-name-denied; permission gate never fires under bypass;
  the read endpoint already returns `[]`). Migrating carries dead machinery.
- **Lane items reuse `data-entity` for `?focus` (rejected, D7).** Collides with
  the 039 card anchor; `?focus` would silently retarget from card to lane row.
  Resolved with a distinct `data-nyi-entity` and leaving `?focus` on the card.
- **Single approver scope for mode (rejected, D2).** Either `list_pending('user')`
  or `list_pending(maestro)` alone re-creates the disjoint-set bug. The union is
  the only complete set.
- **Priority left exposed-but-undefined (rejected, D9).** Invites each consumer to
  invent its own ordering — a mini re-fragmentation. Pinned as "server pre-sorts,
  consumers must not re-sort".

---

## Deferred / out of scope

Each item below is real work but belongs to another ticket; naming it here keeps
051's boundary sharp.

- **Push-side deep-link repair for mode/vault** → **041's domain.** `web_push.py`
  builds `/?reply=<entity>` from `data['entity']` (`:45-61`); mode/vault push
  payloads don't set that field, so their push deep-links are already broken. 051
  normalizes `entity` **only inside the rollup** (for the lane's own fields); it
  does **not** touch `web_push.py` or `ALERT_KINDS`. Fixing the push side is a
  041 follow-up (its own ticket).
- **Adding `gate`/`errored` to `ALERT_KINDS`** (so they trigger Web Push) →
  **041's domain.** `web_push.py` has no `else` branch (would fail silently on an
  unhandled ALERT kind, Q11). Out of 051.
- **A new `needs_you` wire notification kind** → **not built.** Would break the
  cross-surface `ALERT_KINDS` contract (D6). The rollup is a server-side view, not
  an event.
- **052's hero headline copy** ("N loops running", calm/loud framing) → **052.**
  051 ships only the bare "✓ all clear" lane line (D10).
- **053's tab navigation wiring** (binding the item header → the right Work-view
  tab) → **053.** 051 ships the header as a bindable open-tab target with a stable
  `data-nyi-entity` anchor (D9); it does not build the tab router.
- **Mid-run Workflow steering / new observability widgets** → sprint out-of-scope.
- **Splitting `errored` to its own follow-up ticket** → **only if the window
  tightens.** Errored is the designated 051 de-scope lever (D3): it shares no data
  path with decision/mode/vault, so it lifts cleanly into a follow-up.

---

## Risks / open items for `outline.md`

- **Errored is the biggest single build** (PC-3, D3): no existing surface or test
  to lean on. Carry it as a **first-class line item** with its own budget and its
  own view-model + synthesized-summary tests — not a rider on the rollup. It is
  the clean de-scope lever if the window slips.
- **Shared web-file rebasing** (sprint risk): 051/052/053 all touch
  `landing.html` + `view_model.py` + `service-worker.js`. Land order matters; **051
  owns the `hive-v7` CACHE_VERSION bump**, 052/053 rebase onto it (don't re-bump).
- **`/reset` UX guard** (D3): confirm the errored button has a confirm affordance
  and is visually distinct from approve/deny before ship.
- **iPad re-smoke:** the delegated-handler-survives-swap behaviour, the header
  vs action tap split (D9), and the calm empty state have no JS test harness (Q15)
  — must be verified on a real installed PWA, not just green units.
