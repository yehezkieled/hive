# Design — Ticket 043: standalone PWA status-bar overlap

## Chosen approach — reserve the top safe-area inset on `.top-bar`

Keep `apple-mobile-web-app-status-bar-style: black-translucent` (040's
edge-to-edge install look) and add the missing **top** inset reservation to the
single shared `.top-bar` rule in `src/hive/web/static/landing.css`.

```css
.top-bar {
  /* was: height: 52px; */
  height: calc(52px + max(0px, env(safe-area-inset-top)));
  ...
  padding-left:  max(0px, env(safe-area-inset-left));
  padding-right: max(0px, env(safe-area-inset-right));
  padding-top:   max(0px, env(safe-area-inset-top));   /* NEW */
}
```

**Why this shape:**

- **Grows the bar, not just pads it.** Under `* { box-sizing: border-box }`
  (`landing.css:36`), `height` is the border-box, so the `+ env(...)` term keeps
  the content box a full 52px while shifting the whole bar below the status bar.
  `padding-top` alone would squish the content (research Q3).
- **`max(0px, env(...))` mirrors the existing left/right guard.** Off-device
  insets are 0 → `calc(52px + 0)` = 52px and `padding-top: 0` → desktop and
  in-Safari are byte-identical (acceptance: "unchanged"). Only standalone, where
  the inset is non-zero, changes.
- **One rule, both pages.** Landing (`/`, the `start_url`) and dashboard both
  use this `.top-bar` (research Q1) — the single edit covers both.
- **Self-contained.** Nothing else keys off a 52px bar height; `.top-bar` is
  `position: relative`, so the grow pushes content down in normal flow (research
  Q4).

## Alternatives considered

- **#1 — `status-bar-style: default`** (`_pwa_head.html`). One-line meta change;
  iOS gives the status bar its own opaque strip and starts the app below it.
  **Rejected:** discards the edge-to-edge look 040 deliberately chose, and
  changes install chrome for a cosmetic reason. The translucent route is the
  smaller behavioural change.
- **Add `padding-top` without growing `height`.** **Rejected:** border-box makes
  this a non-fix — it shrinks the content box and slides the bar contents *under*
  the clock (research Q3).
- **A `@media (display-mode: standalone)` wrapper.** Unnecessary — the
  `max(0px, env(...))` guard already self-zeroes off-device, so no media query is
  needed to scope the change to standalone.

## Side effects

- **`CONTEXT.md`:** none — no new term.
- **ADR:** none — this is a regression fix inside decisions already recorded
  (040's `black-translucent`; ADR 0023 for the HTTPS origin). No architectural
  choice is being made.
- **Reference docs (`DEPLOYMENT.md` / `README.md`):** none.

## Verification (defines the build PR's done-ness)

- `ruff` is irrelevant (CSS only); no Python touched.
- **On-device re-smoke is the real gate** — installed PWA on an actual iPad,
  launched from the home screen (standalone), **portrait and landscape**: the
  status bar no longer overlaps the brand/tabs/bells/terminal button.
- Confirm **desktop** and **in-Safari (non-standalone) iPad** are visually
  unchanged.

## One-shape ticket → no `outline.md`

A single two-line edit to one CSS rule has no module structure to sketch; the
file-op lives directly in `plan.md`.
