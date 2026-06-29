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

## On-device correction (2026-06-29) — approach #2 failed, switched to #1

The first build shipped approach #2 (keep `black-translucent`, reserve
`env(safe-area-inset-top)` on `.top-bar`; PRs #228/#229). The on-device iPad
re-smoke **still overlapped**. Root cause: on iPad (no notch), iPadOS reports
`env(safe-area-inset-top)` as **0** in standalone mode, so `max(0px, env(...))`
computed to zero padding — the reservation never happened. The research/design
above was iPhone-correct but iPad-blind; the env() approach can't work where the
inset is 0.

**Switched to the rejected alternative #1 — `apple-mobile-web-app-status-bar-style:
default`** (`_pwa_head.html`). With `default`, iOS gives the status bar its own
opaque strip and starts the web view *below* it — no inset math, correct on every
device and orientation. The `.top-bar` env-top padding was reverted (dead under
`default`, since the inset is then always 0). Trade-off accepted by the developer:
lose 040's edge-to-edge translucent look for guaranteed correctness on the iPad
daily driver. `CACHE_VERSION` bumped `hive-v3 → hive-v4` so installed PWAs flush
the old shell.

## On-device correction #2 (2026-06-29) — `default` alone also failed; the real fix

`status-bar-style: default` (#235) **still overlapped** after a clean iPad
remove + re-add. A researched, sourced pass (Apple Safari HTML Reference, an
Apple engineer's forum post, multiple 2023-2025 PWA write-ups) pinned the true
mechanism: **`viewport-fit=cover`** (Ticket 037) makes the web view full-bleed
under the status bar *regardless of `status-bar-style`* — the style only sets the
bar's appearance, not whether content sits under it. Combined with
`env(safe-area-inset-top) == 0` on a non-notch iPad, nothing reserved the space.

**Final fix — reserve a fixed top strip, gated to standalone:**

```css
@media all and (display-mode: standalone) {
  .top-bar {
    height: calc(52px + max(env(safe-area-inset-top), 24px));
    padding-top: max(env(safe-area-inset-top), 24px);
  }
}
```

Load-bearing detail: **`max(env(...), 24px)`**, not `env(.., 24px)` — env's
fallback argument only fires when the value is *undefined*; iPad reports `0` (a
defined value), so the fallback never triggers. `max()` floors at the 24px iPad
status-bar height and still grows to the real notch inset on iPhone. Gated to
`display-mode: standalone` so the in-Safari tab (env == 0 is correct there) gets
no phantom gap. **`viewport-fit=cover` is kept** — removing it would collapse
*every* safe-area inset to 0 and break 037's composer / terminal-bar / drawer /
notch handling. `status-bar-style: default` is kept too, now purely for **dark
icons** visible over the light paper strip. `CACHE_VERSION → hive-v5`.

Also folded in: the **Dashboard tab misalignment** — it is the only `<a>` among
`<button>` tabs, so without a pinned `display`/`line-height` the `.tab` rule let
it inherit `line-height: 1.4` and sit a few px high. Fixed by normalizing `.tab`
to `display: inline-flex; align-items: center; line-height: 1`.

**Lesson:** the env()-padding approach (and the `status-bar-style` toggle) are
iPhone-shaped; iPad needs a constant floor via `max()`. A CSS/PWA fix here is
only observable in the installed standalone PWA — gate on the on-device re-smoke,
not curl/pytest.
