# Research — Ticket 043: standalone PWA status-bar overlap

Findings with code refs. Every claim is grounded in the current tree; the one
genuinely un-codeable claim (the visual result in standalone mode) is flagged
**VERIFY ON-DEVICE**.

## Q1 — Where is `.top-bar`, and who shares it?

**One rule, shared by both pages.** `.top-bar` is defined once, in
`src/hive/web/static/landing.css:96`.

- `start_url` is `/` (`src/hive/web/static/manifest.webmanifest` →
  `"start_url": "/"`, `"display": "standalone"`). So the installed PWA opens
  the **landing page**.
- `/` → `landing.html` (`src/hive/web/app.py:541`), which loads
  `landing.css` (`landing.html:11`) and renders `<header class="top-bar">`
  (`landing.html:20`).
- `/dashboard` → `dashboard.html` (`app.py:570`), which loads **both**
  `landing.css` and `dashboard.css` (`dashboard.html:11-12`) and uses the
  **same** `<header class="top-bar">` (`dashboard.html:20`). `dashboard.css`
  defines no `top-bar` / safe-area / status-bar rule of its own.

→ **Fixing the single `.top-bar` rule in `landing.css` fixes both pages.**

## Q2 — Did 037 add *top* safe-area padding? No.

The `.top-bar` rule (`landing.css:96-110`) reserves the safe area on **left and
right only**:

```css
.top-bar {
  height: 52px;
  ...
  position: relative;
  z-index: 3;
  /* Safe-area: keep the bar clear of the notch / rounded corners (D5).
     max() with 0px means desktop (insets == 0) is byte-identical. */
  padding-left: max(0px, env(safe-area-inset-left));
  padding-right: max(0px, env(safe-area-inset-right));
}
```

There is **no `padding-top`**. The ticket's hypothesis ("037's padding isn't
taking effect — gated to a media query, or computing as 0") is **wrong**:
nothing is gated and nothing computes to 0. 037 only ever addressed the notch
on the **sides** (its "D5" comment); the **top** inset was never reserved. This
is a missing rule, not a broken one — which is why no on-device debugging is
needed to *find* the cause (it was always going to overlap under
`black-translucent`).

`black-translucent` itself comes from Ticket 040
(`src/hive/web/templates/_pwa_head.html:8`), which tells iOS to extend the web
view **under** the status bar — exactly the mode that requires the top chrome to
reserve `env(safe-area-inset-top)`.

## Q3 — box-sizing + fixed height: the trap

`src/hive/web/static/landing.css:36` sets the global reset:

```css
* { box-sizing: border-box; }
```

With `box-sizing: border-box`, the declared `height: 52px` is the **border-box**
height (content + padding). So a naive `padding-top: env(safe-area-inset-top)`
would **eat into** the 52px — the brand/tabs content box shrinks to
`52px − inset` and slides *under* the status bar. That is a non-fix (it looks
worse, not better).

→ The fix must **also grow the height** by the inset, so the content box stays a
full 52px and the whole bar shifts down:

```css
height: calc(52px + max(0px, env(safe-area-inset-top)));
padding-top: max(0px, env(safe-area-inset-top));
```

The bar's `background: var(--paper)` fills the whole border-box (incl. the
padding strip), so the paper colour extends up under the translucent status bar
— preserving the edge-to-edge look 040 chose. **VERIFY ON-DEVICE** (the visual
is only observable in standalone mode).

## Q4 — Does anything depend on a 52px top bar? No.

- `grep 52px` over `landing.css` + `dashboard.css` returns **only** the
  `.top-bar { height: 52px }` declaration itself (`landing.css:97`). No
  `top: 52px`, no `padding-top: 52px`, no `calc(... 52px)` anywhere.
- `.top-bar` is `position: relative` (`landing.css:104`) — it sits in normal
  flow, so growing its height naturally pushes the content below it down. No
  `position: fixed` sibling is pinned at a 52px offset (the fixed elements at
  `landing.css:679/750/1531/1578/1709` are drawers / overlays / the token modal,
  none keyed to the bar height).
- The token modal already correctly reserves the top inset itself
  (`landing.css:1715`: `padding: max(48px, env(safe-area-inset-top)) ...`) and
  is independent of `.top-bar`.

→ Growing `.top-bar` is **self-contained** — no knock-on offsets to chase.

## Q5 — Which candidate fix? #2, and the choice is now settled in code.

| | #1 `status-bar-style: default` | #2 keep `black-translucent` + reserve top inset |
|---|---|---|
| Look | Opaque OS strip above an inset app — loses 040's edge-to-edge | Paper bar runs under a translucent status bar — keeps edge-to-edge |
| Surface | `_pwa_head.html` meta (1 line) | `landing.css` `.top-bar` (2 lines) |
| Risk | Changes the install look 040 deliberately chose | Byte-identical off-device (insets == 0) |
| On-device debug to *find* fix | n/a | **None** — Q2/Q3 already explain the cause |

→ **Choose #2.** The ticket flagged #2 as "needs the on-device debug to find why
037's padding isn't enough" — but Q2 shows there *was* no top padding to debug,
so that caveat is void. #2 keeps the chosen aesthetic and is the smaller, safer
diff. The only on-device step left is **verification**, not investigation.

## Q6 — Desktop / in-Safari unchanged? Yes.

`env(safe-area-inset-top)` resolves to **0** when there is no inset:

- **Desktop:** no safe area → `calc(52px + max(0px, 0)) = 52px`, `padding-top:
  0`. Byte-identical to today.
- **In-Safari on iPad (non-standalone):** Safari's own chrome occupies the top,
  so `env(safe-area-inset-top)` is 0 there too → unchanged.
- **Standalone PWA:** inset is non-zero (≈20px portrait) → bar grows and
  reserves the strip. This is the only context that changes — matching the
  acceptance criteria exactly.

The `max(0px, …)` guard mirrors the existing left/right pattern, so the desktop
path is provably a no-op.

## Open / for the implementer

- The fix is **standalone-only observable**. Unit/curl/in-Safari checks cannot
  confirm it — the build PR's verification is a **deployed re-smoke on an actual
  iPad, installed PWA, portrait + landscape** (per acceptance).
- No new ADR or `CONTEXT.md` term — this is a CSS regression fix within the
  040/037 decisions already recorded (ADR 0023 for the HTTPS origin;
  `black-translucent` chosen in 040).
