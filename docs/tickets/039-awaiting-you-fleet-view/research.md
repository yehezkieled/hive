# 039 — Research

Code-grounded answers to `questions.md`. Source: a 6-track parallel read of
`src/hive/web` + the stores/models it reads (all claims carry `file:line`).

## Headline: the ticket's premise is half-wrong — read this first

The ticket says *"the data is already computed in `view_model`; this is a
surfacing ticket, not new backend logic."* **That is false at the per-node
level**, confirmed by reading the code directly:

- `_entity_to_card` (`view_model.py:125-156`) returns
  `{name, role, state, summary, updated, leads, active_runs, runs, tasks, mode,
  model}` — **no awaiting / decision / blocked key.**
- What *does* exist is a single **global aggregate**: `pending_total =
  len(vault_pending) + len(mode_pending)` (`view_model.py:253`), surfaced as the
  bell `approvals_count` (`:313`) and `vault.pending_approvals` (`:327`),
  scoped to the default maestro only.

So the per-node signal the badge needs **does not exist yet**. **039 is a
`view_model` + template ticket, not template-only.** This is the single most
important correction; everything below follows from it.

## Q11 (surface) — settled: Jinja landing page, NOT the JSX dashboard

There are two web surfaces; the entity cards live on **`/` (Jinja)**, not
`/dashboard` (JSX):

```
GET /                                  GET /dashboard
  → templates/landing.html               → templates/dashboard.html = <div id=root> shell
  → maestro_card (_macros.html:71-105)    → mounts React+Babel from a CDN
  → run_card    (_macros.html:54-69)      → renders ONLY 8 observability widgets
  → htmx polls /api/landing/* 5/15/30s       (cost/health/burn/matrix/cache/audit/
  → server-rendered fragments                 failure/CFD). "entity" = a chart axis.
  → build_landing_view_model (:194)           NO entity cards / org nodes / run-cards.
```

`dashboard.html` imports only `bee` + `state_dot` macros — **not** `maestro_card`;
the JSX (`dashboard-mount.jsx:26-76`) has zero entity/org/run markup.
**Building the badge in any `static/dashboard/*.jsx` would put it where no cards
exist.** Correct home: `_macros.html` (markup) + `view_model.py:_entity_to_card`
(data) + `landing.css` (style). **Do not touch `dashboard.css`** — it's a 33-line
scaffold; the real fleet stylesheet is `landing.css` (1425 lines).

## Q1/Q9 (state + transport) — three blocked-on-user sources, one is clean

There is **no single "blocked on user" concept**. Three independent sources,
none rolled up per node:

| # | Source | Where | Durable? | Clears via |
|---|--------|-------|----------|------------|
| **A** | `Entity.awaiting_decision: bool` (029 channel) | set `message_dispatcher.py:472`, field `entity.py:229` | **yes** (migration 029; `entity_store.py:39/61/82/155`) | `manager.clear_awaiting_decision` (`manager.py:214-241`), user-path only |
| **B** | `is_parked_at_gate(name)` (003 gates) | `manager.py:357-370` (in-mem predicate) | no (in-mem) | gate answered |
| **C** | pending `mode_requests`/`vault_actions` rows | `view_model.py:243-253` | yes (DB) | approve/deny endpoints |

**Source A is the cleanest:** durable, per-entity, orthogonal to lifecycle (an
IDLE entity can still be awaiting — `entity_store.py:152-155`), and it already
fires a `decision_request` SSE event on SET (`message_dispatcher.py:467`).

**Transport:** the cards re-render **only** via htmx interval polling —
active/idle every 5s, vault 15s, hero/dormant 30s (`landing.html:143-188` →
fragments `app.py:450-473`), full-`innerHTML` swaps. SSE
(`/sse/notifications`, `app.py:401-418`) is a separate event stream that drips
chat lines; it never re-renders cards. **The trap:** SET fires
`decision_request`; **CLEAR fires nothing** (`clear_awaiting_decision` has no
`_notify`). So an SSE-driven badge would light and stick forever. The 5s htmx
poll, by contrast, sets *and clears* the badge for free **once the field is in
the card dict** — but it can't help today because the field isn't there.

## Q4/Q5/Q6 (hierarchy) — there is no org tree on the landing page

The landing page is **flat sections** (Pinned / Active / Idle / Dormant), not a
tree. Critically:

- **Only Maestros are cards.** `build_landing_view_model` drops non-maestros:
  `maestros = [e for e in entities.values() if isinstance(e, Maestro)]`
  (`view_model.py:206-207`). A **Lead appears only as the `NL` integer**
  (`leads = len(entity.teams)`, `view_model.py:132-134`; rendered `_macros.html:93`).
  → "a badge on a waiting Lead's node" has **no UI to attach to** today.
- **Runs can't block** (ADR 0014, read-only): `_runs_for`
  (`view_model.py:97-122`) builds `{run_id, name, phase, status, done_count,
  agent_count}` — no awaiting field, and it has **no access to the Lead object**.
  A run badge could only *mirror* its Lead, needing new plumbing.
- **No rollup exists.** The only cross-entity aggregate is the global
  `pending_total`. But the raw Lead objects (with their own `awaiting_decision`)
  *are* reachable via `process_manager.entities` (`manager.py:332-333`); the
  view-model just discards them. The `f"{maestro.name}."` prefix idiom already
  used by `_open_tasks_for` (`:77-94`) is how a rollup would gather them.

→ This is **design fork B** (below): roll up to the maestro card, or build
lead-level nodes (bigger; brushes the ticket's own S9+ non-goal).

## Q12 (038 seam) — confirmed independent

`/api/decision` **does not exist** — it's 038's deliverable (grep: no
`api/decision`/`DecisionStore`). The `decision_request` payload carries only
`data={'entity': name}`, not the question text — 038 enriches that. **039 reads
the durable `entity.awaiting_decision` boolean directly and needs none of 038's
new payload/store.** Independent. ✅

## Q13 (037 collision) — real but narrow

037 lands first and edits the same `src/hive/web` files. The one true CSS
collision: 037 **rewrites the `@media (max-width:900px){.chat-rail{display:none}}`
block** (`landing.css:1420-1425`) — that *is* the iPad-portrait bug it fixes.
**039 must NOT edit the `@media` tail**; append badge/chip rules near
`:1020-1160` instead → the 039-onto-037 rebase becomes a clean tail-append.
In `landing.html`/`_macros.html` the overlap is content-level (037 may rewrite
chrome; **038 also edits the same SSE `onmessage` block**). Land order matters;
the later PR rebases.

## Files 039 must touch, by layer

**STATE** — `view_model.py`
- `_entity_to_card` (`:125-156`) — **add an `awaiting_you` key** (read
  `entity.awaiting_decision`; optionally OR gate/approvals per fork A).
- `build_landing_view_model` (`:194-338`) — compute the per-maestro **rollup**
  (own flag OR any lead under `f"{name}."`).
- `_OTTER_STUB` (`:179-191`) — **must get the new key too**, or the pinned PA
  card KeyErrors on cold start.
- `idle_list` (`:220-228`) — add the flag if idle-but-waiting must badge (see
  state-collapse gotcha).

**TRANSPORT** — only if instant (<5s) wanted
- `manager.clear_awaiting_decision` (`:214-241`) — add a clear `_notify` **only
  if** going SSE (else badge sticks). `_notify` auto-fans to SSE
  (`manager.py:739-749`, `__main__.py:378-379`).

**RENDER** — Jinja
- `_macros.html` `maestro_card` (`:71-105`) — badge markup (near
  `maestro-card__addr` `:77` or beside `maestro-card__chip` `:80`). One edit
  covers pinned PA + all active cards.
- `landing.html` — filter chip in a `.section-head__right` slot (`:165`); chip
  JS **must be document-delegated** (like `[data-cmd]` `:661-671`) — htmx swaps
  destroy direct listeners.
- `_partials/idle.html` (`:3-10`) — own badge slot if idle entities badge.
  `dormant.html` = unspawned → can't be awaiting → skip.

**STYLE** — `landing.css`
- Badge: clone `.bell__count` (`:202-218`, absolute/red/circular + `a3-badge`
  pulse `:1400-1403`); `.maestro-card` is already `position:relative`
  (`:1033-1047`) with `.maestro-card__pin` (`:1055-1068`) as the corner-badge
  precedent.
- Chip: clone `.composer__chip` (`:633-646`) or `.dormant-pill` (`:1350-1365`);
  **size ≥44px** (037 DoD — existing chips are 22–34px).
- **Run-cards have ZERO CSS anywhere** (`run-card*`/`maestro-card__runs`); badging
  them means new styling incl. `position:relative` from scratch.

## Gotchas (carry into design/plan)

1. **Premise-false** — `_entity_to_card` has no awaiting field; 039 is
   view_model + template, not template-only.
2. **`_OTTER_STUB` KeyError** — any new card key must be added to the stub.
3. **Silent CLEAR** — SET fires SSE, CLEAR fires none → an SSE badge sticks.
   Poll-only avoids this.
4. **htmx kills listeners** — badge must be server-rendered; chip JS delegated.
5. **State-collapse hides the badge when it matters most** — `_display_state`
   (`:56-74`) buckets by a ~10-min recency window; an entity parked on
   `awaiting_decision` falls to **IDLE** after 10 min, and `idle.html` has no
   badge slot. Badge idle too, or it vanishes exactly when you need it.
6. **Duplicate "awaiting you" copy** — already on the vault card
   (`vault.html:23`) and bell (`landing.html:37-40`, mode/vault approvals).
   Differentiate the wording so the 029 badge doesn't read as the same thing.
7. **Approval attribution (fork-A only)** — `list_pending(default_maestro)` is
   scoped to the default maestro as *approver*; per-node needs grouping by
   `requester`, and non-default-maestro approvals may not be fetched. Confirm a
   `requester` value always equals a live entity name — **CONFIRM IN CODE**.

## Design forks (resolved in `design.md` after the grill)

- **A — sources:** badge = `awaiting_decision` only (cleanest, durable), or also
  fold in gate-parked (B) and mode/vault approvals (C)?
- **B — hierarchy:** maestro-only **rollup** ("anything under me waiting"), or
  emit **lead nodes** as first-class cards (bigger; brushes S9+ non-goal)?
- **C — filter transport:** client-side body-class toggle vs server-side param.
- **D — live update:** poll-only (5s, free clear) vs SSE (instant, needs clear
  event).

## Still `CONFIRM IN CODE`

Verified directly: `_macros.html:54-105` (Jinja, no badge), `_entity_to_card`
dict (no awaiting key), `_runs_for` has no Lead object. Asserted by readers, not
re-verified: the exact `entity.py:229` / `message_dispatcher.py:472` /
`manager.py:214-241` / `gate_coordinator.py:95-101` line numbers, and whether a
`vault_actions`/`mode_requests.requester` is always a live entity name.
