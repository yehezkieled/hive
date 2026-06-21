# 039 — Design

Chosen approach, decided forks, and the alternatives rejected. Grounded in
`research.md` (file:line evidence there; not repeated).

## Decisions (the four forks)

| Fork | Decision | Why |
|------|----------|-----|
| **A — sources** | Badge = `awaiting_decision` **OR** `is_parked_at_gate`. **C** (mode/vault approvals) stays on the existing bell. | A+B are the two "frozen until you reply" states with **no** per-card surface today. C already has a count + popup; per-node C needs messy `requester` grouping with a default-maestro-scope caveat for little gain. |
| **B — hierarchy** | **Maestro-rollup.** A maestro's `awaiting_you` = own flag OR any lead under `f"{name}."`. **No new lead/run cards.** | The sprint frames 039 as the *cheap* attention router; the ticket's own non-goal defers the fleet board to S9+. Rollup still routes attention ("otter needs you" → tap in). Lead cards are a separate, larger ticket. |
| **C — filter** | **Client-side** body-class toggle, document-delegated listener. | No new endpoint/auth; survives htmx 5s `innerHTML` swaps (the body isn't swapped; cards carry a server-rendered class). Matches the existing `[data-cmd]` delegated idiom. |
| **D — live update** | **Poll-only** (existing 5s htmx swap). No SSE. | The 5s swap sets *and clears* the badge for free once the field is on the card. SSE SET fires but CLEAR fires nothing → an SSE badge would stick. Poll-only sidesteps the silent-stick bug entirely. |

**"Works across the hierarchy" is honored as:** the maestro badge **rolls up**
everything beneath it (own + leads), not a literal per-lead badge. This is the
explicit reinterpretation of the ticket's acceptance line, approved in the grill.

## What gets built

### 1. State — one boolean per card (`view_model.py`)

Add an `awaiting_you: bool` key to the maestro card. Computed in
`build_landing_view_model` where the Lead objects are still reachable
(`process_manager.entities`), since `_entity_to_card` only sees one entity:

```python
def _is_awaiting(pm, entity) -> bool:
    return bool(getattr(entity, "awaiting_decision", False)) \
        or pm.is_parked_at_gate(entity.name)

# in build_landing_view_model, per maestro card:
leads = [e for e in process_manager.entities.values()
         if e.name.startswith(f"{maestro.name}.")]          # same prefix idiom as _open_tasks_for
card["awaiting_you"] = _is_awaiting(process_manager, maestro) \
    or any(_is_awaiting(process_manager, lead) for lead in leads)
```

Three insertion points must all carry the key or the macro breaks:
- `_entity_to_card` / the active-card path — the real maestro cards.
- **`_OTTER_STUB` (`:179-191`)** — the hardcoded PA fallback; **must** get
  `awaiting_you` (read the live otter entity if present, else `False`) or the
  pinned card KeyErrors on cold start.
- **`idle_list` (`:220-228`)** — add `awaiting_you` per idle entity, because
  `_display_state` collapses a parked entity to IDLE after ~10 min — the moment
  the badge matters most. (Idle uses its own thin dict + `idle.html`.)

> Runs are **not** touched: a Workflow run has no blocked state (ADR 0014) and
> `_runs_for` has no Lead object. The rollup already reflects a blocked lead on
> the maestro card; that is the run-level signal, surfaced one level up.

### 2. Render — badge markup (`_macros.html`, `_partials/idle.html`)

`maestro_card` (`:71-105`), in the head row near `maestro-card__chip` (`:80`):

```jinja
{% if m.awaiting_you %}<span class="awaits" title="Blocked — awaiting your reply">● you</span>{% endif %}
```

Plus `class="... {{ 'is-awaiting' if m.awaiting_you else '' }}"` on the card root
(the filter hook). Same compact badge + `is-awaiting` on the idle strip
(`idle.html:3-10`). **Copy is deliberately `● you`, not "awaiting you"** — the
vault card (`vault.html:23`) and bell already use "awaiting you" for *approvals*;
the card badge must read differently to avoid conflation (research gotcha 6).

### 3. Filter — one chip, body-class toggle (`landing.html`, `landing.css`)

A **"Waiting on me"** chip in static chrome (the Active `.section-head__right`
slot `:165`, confirmed outside the htmx swap target so its pressed state
persists). Clicking toggles `body.show-awaiting-only`. A document-delegated
handler (alongside the `[data-cmd]` one at `:661-671`) flips the class and a
pressed style. CSS:

```css
body.show-awaiting-only .maestro-card:not(.is-awaiting),
body.show-awaiting-only .idle-row:not(.is-awaiting) { display: none; }
```

Because every card re-renders server-side with its `is-awaiting` class each 5s
swap, and the body class lives on the un-swapped `<body>`, the filter is correct
across polls with zero per-swap JS. **Empty state:** on toggle-on the handler
counts `.is-awaiting`; if zero, it reveals a static "Nothing needs you right
now" line (hidden otherwise).

### 4. Style — reuse existing primitives (`landing.css`)

- Badge `.awaits`: clone `.bell__count` (`:202-218`) — small, red
  (`var(--accent)`), with the `a3-badge` pulse (`:1400-1403`). `.maestro-card`
  is already `position:relative` (`:1033-1047`); `.maestro-card__pin`
  (`:1055-1068`) is the corner-badge precedent. For the idle strip, inline (not
  absolute).
- Chip: clone `.composer__chip` (`:633-646`); **size ≥44px tap target**
  (037 DoD — existing chips are 22–34px), with a `.is-pressed` variant.
- **Append all rules near `:1020-1160`. Do NOT edit the `@media` tail
  (`:1420-1425`)** — that's 037's exact rewrite zone. Keeps the 039→037 rebase a
  clean tail-append.

## Alternatives rejected

- **Lead/run nodes as first-class cards (Fork B-b)** — rejected: it's the S9+
  fleet board the non-goals defer; large (new node emission, lead macro, layout,
  CSS). The rollup covers the attention-routing need cheaply.
- **Fold mode/vault approvals into the badge (Fork A-C)** — rejected: already on
  the bell; per-node attribution is messy (`requester` grouping, default-maestro
  scope) for marginal value.
- **SSE-driven badge (Fork D)** — rejected: CLEAR fires no event → badge sticks;
  would force a new `_notify` in `clear_awaiting_decision` and a new
  `decision_request` branch in the SSE client that 038 is already editing.
  Poll-only is simpler and collision-free.
- **Server-side filter param (Fork C)** — rejected: needs a new endpoint and
  doesn't survive across the two independent sections as cleanly as a body class.
- **Building the badge in the JSX dashboard** — rejected: that surface renders
  zero entity cards (research §Q11).

## Side effects: reference docs

- **No ADR.** This surfaces existing state through existing transport; no
  append-only architectural decision is being locked in. The rollup semantics
  live here in `design.md`.
- **No `CONTEXT.md` change.** "Awaiting-you badge" / "Waiting-on-me filter" are
  UI labels, not new domain entities; the underlying terms (`awaiting_decision`,
  interactive gate) are already glossary'd.
- **No `README`/`DEPLOYMENT`/`ARCHITECTURE` change** — no new route, service, or
  runbook step.

## Verification approach

- Unit: `build_landing_view_model` sets `awaiting_you=True` for (i) a maestro
  with `awaiting_decision`, (ii) a maestro with a gate-parked lead under it,
  (iii) `False` otherwise; `_OTTER_STUB` and `idle_list` carry the key.
- Render: `maestro_card` / `idle.html` emit the badge + `is-awaiting` class iff
  the flag is set.
- Deployed re-smoke on an actual iPad (Safari mounts htmx/JS; curl-200 is
  insufficient per CLAUDE.md): trigger a real `request_decision`, see the badge
  appear within ~5s, toggle the chip to isolate it, answer, see it clear.
