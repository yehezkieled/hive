# Outline — Ticket 037: Responsive / touch shell

Ordered implementation structure for **one builder, one PR** (direct lane). Each
step names the files and the decision (`Dn`) it implements. Order matters:
viewport/safe-area groundwork first (later rules consume the insets), drawer CSS
before its JS, attention wiring after the drawer exists.

> **Scope fence:** only `src/hive/web/templates/landing.html` +
> `src/hive/web/static/landing.css`. **No** new files; **no** `/dashboard` JSX;
> **no** PWA manifest/SW (040) or decision API (038). The composer keydown
> block `landing.html:640-660` is **read-only** (the Enter-to-send hotfix).

## Steps

1. **Viewport meta + safe-area groundwork** — *D5*
   `landing.html:5` → `…, viewport-fit=cover`. `landing.css`: `env(safe-area-inset-*)`
   via `max()` on `.top-bar` (`:89`), `.terminal-bar` (`:1368`), `.composer` (`:586`).
   *First* — `viewport-fit=cover` makes the insets nonzero; later rules are dead
   without it. Unblocks 040.

2. **Drawer + scrim CSS** — *D1, D2, D7-handle*
   `landing.css`: replace `1423-1425` (`display:none`) with the fixed-overlay
   drawer (`inset:0`, `transform:translateX`, `.shell.drawer-open` reveal,
   `bottom: calc(env(safe-area-inset-bottom) + var(--kb))`). Add `.drawer-scrim`
   (z-39), the narrow-only **44×44 `.drawer-toggle`**, hide the resizer at
   `<=900px`, raise `.top-bar`/`.terminal-bar` z-index above 40. Add
   `prefers-reduced-motion`, `.sr-only`, `focus-visible` rings.

3. **Drawer markup** — *D1, D8-markup*
   `landing.html`: `.drawer-toggle` (with `aria-expanded`/`aria-controls`/`sr-only`
   name) in the top bar (`:27-47`); close-x in the rail head (`:55-62`);
   `.drawer-scrim` + a visually-hidden `aria-live="assertive"` node as `.shell`
   children (`:16`); `role="dialog"`/`aria-modal`/`aria-labelledby` on the `<aside>`.

4. **Drawer control JS (IIFE)** — *D1, D2, D8*
   New IIFE near `landing.html:683`: toggle adds `.shell.drawer-open`; **single
   close path** for scrim-`pointerdown` / Escape / close-x. On open: `inert` the
   `.top-bar`+`.content`, clear rail `inert`, focus close-x, scroll-lock
   `.content-inner`. On close: clear background `inert`, set rail `inert` (narrow),
   restore toggle focus, unlock. A `matchMedia('(max-width:900px)')` change
   listener + `orientationchange` drive teardown.

5. **SSE attention wiring** — *D8, seeds 039*
   `landing.html`: in `appendModeRequestBubble` (`:419`) and
   `appendVaultRequestBubble` (`:359`) write the requester into the `aria-live`
   node; if the drawer is closed set an unread dot + update the toggle
   `aria-label`; re-run scroll-to-bottom on open. Without this a decision rots
   off-screen behind a shut drawer with hidden focusable Allow/Deny buttons.

6. **Tap-target pass** — *D3*
   `landing.css`: one new `@media (pointer:coarse)` block. Flow buttons
   `min-height:44px` (`.btn` `:287`, `.btn-sm` `:1139`, `.mode-req__btn` `:541`,
   `.tab` `:138`, `.terminal-btn` `:219`); icon buttons 44×44 (`.composer__send`
   `:678`, `.composer__attach` `:697`, `.bell` `:187`, help-popup close); inset
   `::before` hit-box for tiny glyphs (`.participant__close` `:338`,
   `.composer__chip--help` `:647`, `.composer__attach-chip button` `:729`).
   `touch-action:manipulation` + global transparent tap-highlight. The
   `.mode-req__btn` bump covers all four JS render paths (`:375-376, 433-434,
   1042-1043, 1163-1164`).

7. **Hover gating** — *D4*
   `landing.css`: strip the ~18 lift `:hover` rules from the base; re-add inside
   one `@media (hover:hover)` block (`:152,200,234,260-261,299,335,557,563,645,
   658,691,710,787,819,1006,1048-1051,1154,1175,1321-1324`, inline `:1277-1278`).
   Keep `:active` (`:300,550,646`) in the base.

8. **Keyboard handling JS** — *D6*
   `landing.html`: new `visualViewport` IIFE near `:683` — `resize`/`scroll`/
   `orientationchange` write `--kb` via `requestAnimationFrame`. Fix
   `positionPopup` (`:894-906`) both `innerHeight` refs (`:897,904`) to
   `visualViewport.height` (fallback `innerHeight`) offset by `vv.offsetTop`.
   `scrollIntoView({block:'nearest'})` on composer focus as belt-and-suspenders.

9. **Resizer guard + hotfix preservation** — *D7*
   `landing.html`: top of `setupRailResizer` (`:684`) early-return when
   `matchMedia` narrow matches; clear the saved inline width on entering narrow
   (in the shared `matchMedia` change handler). `landing.css`: resizer
   `display:none` in the drawer block. **Zero edits to `640-660`.**

10. **`mousedown` → `pointerdown`** — *D9*
    `landing.html`: swap the 5 outside-close/select listeners (`:771` autocomplete
    select — keep the `preventDefault` focus trick; `:834` autocomplete close;
    `:968`/`:1089`/`:1210` help/bell/gate close). Keep Escape `keydown` as a
    hardware-keyboard nicety.

11. **Lint + units + real-iPad re-smoke** — *DoD gate*
    `ruff check src/ tests/ && ruff format --check src/ tests/` (separate gates);
    full `pytest -m "not integration"`; deploy (`git push` →
    `systemctl --user restart hive.service` → `journalctl` clean) → **smoke from
    the Tailscale IP on an actual iPad, portrait + landscape**. JS/keyboard
    behaviour is only confirmable in real Safari — a curl-200 is insufficient.

## Build sequencing note

Steps 1→2→3→4 are the critical path (the drawer must exist before its JS, the
markup before the control IIFE). 6 / 7 (CSS-only sizing + hover) and 8 / 9 / 10
(JS touch paths) are largely independent of each other and can be done in any
order once the drawer (1-4) is in. 5 depends on 3 (the `aria-live` node) + 4 (the
toggle state). 11 is last.
