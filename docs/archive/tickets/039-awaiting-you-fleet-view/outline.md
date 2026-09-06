# 039 — Outline

Implementation structure, in build order. One vertical change (state → render →
filter → style → test); bottom-up so each step is testable before the next.

## Step 1 — State: plumb `awaiting_you` into the view model

`src/hive/web/view_model.py`

1. Add a module-level helper:
   ```python
   def _is_awaiting(process_manager, entity) -> bool:
       return bool(getattr(entity, "awaiting_decision", False)) \
           or process_manager.is_parked_at_gate(entity.name)
   ```
2. In `build_landing_view_model`, after each maestro card is built, compute the
   rollup over the maestro + its leads (leads via the `f"{name}."` prefix over
   `process_manager.entities`, the `_open_tasks_for` idiom) and set
   `card["awaiting_you"]`.
3. Set `awaiting_you` in **`_OTTER_STUB`** (`:179-191`) — `False` when otter is
   absent, else the live rollup. (Guard the KeyError.)
4. Set `awaiting_you` per row in **`idle_list`** (`:220-228`).

**Test after step 1** (pure backend, no browser):
`tests/web/test_view_model.py` — `awaiting_you` True for own-`awaiting_decision`,
True for gate-parked-lead-under-maestro, False otherwise; key present on stub +
idle rows. Confirms the premise gap is closed.

## Step 2 — Render: badge markup

- `src/hive/web/templates/_macros.html` — `maestro_card` (`:71-105`): emit the
  `.awaits` badge when `m.awaiting_you`; add `is-awaiting` to the card root
  class. Copy = `● you` (not "awaiting you").
- `src/hive/web/templates/_partials/idle.html` (`:3-10`): same badge + class on
  the idle row (inline variant).

**Test after step 2:** render `maestro_card` / `idle.html` with a flagged card →
badge + `is-awaiting` present; absent when flag false.

## Step 3 — Filter: the "Waiting on me" chip

`src/hive/web/templates/landing.html`

1. Add the chip to the Active `.section-head__right` slot (`:165`) — **verify it
   sits outside the htmx swap target** so its pressed state survives the 5s swap.
2. Add a static "Nothing needs you right now" empty-state line (hidden by
   default).
3. Extend the document-delegated click handler (near `[data-cmd]` `:661-671`):
   on chip click, toggle `body.show-awaiting-only` + the chip's `.is-pressed`;
   when toggling on, count `.is-awaiting` and show/hide the empty-state line.

## Step 4 — Style

`src/hive/web/static/landing.css` — **append near `:1020-1160`; never touch the
`@media` tail `:1420-1425`.**

- `.awaits` — clone `.bell__count` (`:202-218`) + `a3-badge` pulse; idle variant
  inline.
- `.chip-awaiting` (+ `.is-pressed`) — clone `.composer__chip` (`:633-646`),
  sized **≥44px**.
- `body.show-awaiting-only .maestro-card:not(.is-awaiting)` and the idle-row
  equivalent → `display:none`.

## Step 5 — Verify

1. `ruff check src/ tests/ && ruff format --check src/ tests/`
2. `pytest -m "not integration"` (full suite, not scoped).
3. Deploy + **iPad re-smoke** (portrait + landscape): fire a real
   `request_decision`, badge appears ≤5s, chip isolates it, answer clears it
   ≤5s. Re-smoke **both `/` and `/dashboard`** (shared `landing.css`).

## Build notes

- **Land-order:** 039 rebases onto 037 (CSS `@media` tail) and 038 (shares the
  `landing.html` SSE/script region — though 039 adds none of its own SSE).
- **One PR**, direct lane — see `plan.md`.
- **CONFIRM IN CODE before coding:** exact line numbers for `entity.py:229`,
  `message_dispatcher.py:472`, `manager.is_parked_at_gate`, and that the Active
  `.section-head` is outside the hx-swap target.
