# 043 — Standalone PWA: iOS status bar overlaps the top bar

> Surfaced by the Ticket 040 home-screen install. In the installed PWA the iOS
> status bar (clock / VPN / battery) renders **on top of** the Hive top bar, so
> the clock sits over the brand. Cosmetic — does not block use. Split out of
> Ticket 042 (item #5) into its own ticket per the developer's call. **S9.**

## What

When Hive is launched from the iPad home screen (standalone mode), the iOS
status bar overlaps `.top-bar` (brand + nav tabs) instead of sitting above it.
Make the top bar clear the status bar in both orientations, with no overlap.

## Why

Root cause: Ticket 040 sets `apple-mobile-web-app-status-bar-style:
black-translucent` (`src/hive/web/templates/_pwa_head.html`), which tells iOS to
extend the web view **under** the status bar and float the clock/battery on top.
That look requires the top chrome to reserve `env(safe-area-inset-top)` of top
padding — and `.top-bar` doesn't, in the standalone context. Ticket 037 added
safe-area padding to the top bar, but it isn't taking effect here (it may be
gated to a media query that doesn't match standalone, or computing the inset as
0). It only shows once the app is installed, so it slipped past the in-Safari
re-smokes.

## Acceptance

- In the **installed PWA** (home-screen launch, standalone), the iOS status bar
  no longer overlaps the top bar — brand, tabs, pills, bells, and the terminal
  button all sit fully below the clock/battery, in **portrait and landscape**.
- Desktop and in-Safari (non-standalone) layouts are **unchanged**.
- Verified **on an actual iPad** (the bug is invisible to a curl / in-browser
  check — it only appears in standalone mode).

## Non-goals

- The other Ticket 042 iPad-polish items (token-entry UX, dead header buttons,
  stale-shell cache, keyboard layout).
- Any shell/layout redesign (037 owns the responsive shell).
- Web Push (041).

## Notes / open

- **Sprint: 2026-Q2-S9.** Surfaced from the 040 install; pairs with the S9 web
  track and Ticket 042's iPad polish.
- **Two candidate fixes** (decide on-device):
  1. **`status-bar-style: default`** — the status bar gets its own opaque strip
     and the app starts below it. Simplest; loses the edge-to-edge look.
  2. **Keep `black-translucent`** + add `padding-top: env(safe-area-inset-top)`
     to `.top-bar` that actually applies in standalone. Nicer; needs the
     on-device debug to find why 037's existing padding isn't enough.
- Small surface: `_pwa_head.html` (meta) and/or `landing.css` (`.top-bar`).
  Needs Safari remote inspector against the installed PWA to confirm the inset.
