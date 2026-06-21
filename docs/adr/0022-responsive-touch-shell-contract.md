# ADR 0022 — Responsive touch-shell contract for the iPad daily driver

- **Status:** Accepted
- **Date:** 2026-06-21
- **Tickets:** [037](../tickets/037-responsive-touch-shell/) (establishes the
  contract); consumed by [038](../tickets/038-web-decision-ui-parity/),
  [039](../tickets/039-awaiting-you-fleet-view/), [040](../tickets/040-pwa-install/)

## Context

Phase 4 makes the web the **primary control surface** for Hive from an iPad. The
landing page (`src/hive/web/templates/landing.html` + `static/landing.css` —
vanilla JS + htmx, **no build step**; the React `/dashboard` grid is a separate
surface and out of scope) was unusable on touch: a `@media (max-width:900px)`
rule set `.chat-rail{display:none}` (`landing.css:1423`), **deleting the only
command/approval input** below iPad-portrait width. Secondary gaps: sub-44px tap
targets, hover effects that latch after a tap, no on-screen-keyboard handling, no
safe-area awareness, a mouse-only drag-resizer.

037 is the **foundation ticket** — 038/039/040 are all judged on the same iPad
surface — so its responsive decisions are a shared contract worth recording once,
not re-litigating per ticket. The page is **dense by design** and must stay
pixel-identical on desktop. The decisions below survived a 3-lens adversarial
review (iOS-keyboard, a11y/focus, desktop-regression) that rejected an initial
composer-only-transform / scrim-only-containment design.

## Decision

One touch-shell contract:

1. **Drawer.** At narrow width (`<=900px`) the chat rail becomes a
   **class-toggled fixed-overlay slide-over** anchored `inset:0` (not between bar
   heights — so safe-area-grown bars can't open a containment seam), with a
   `pointerdown`-dismiss scrim. Chosen over native `<dialog>`+`showModal()` to
   keep the hotfix-bearing chat-form and the htmx/SSE model untouched — so the
   dialog machinery is **hand-built**: the closed drawer and the open-drawer
   background both set **`inert`**; focus moves to the close button on open and
   back to the toggle on close; SSE decisions announce via an **`aria-live`** node
   + an unread toggle badge.
2. **Sizing.** 44px hit targets in **one `@media (pointer:coarse)` block** (input
   device, not viewport width); tiny glyphs grow via an inset `::before` hit-box.
3. **Hover.** All hover-lift rules live behind **`@media (hover:hover)`** (not
   `pointer:fine`, which would drop hover on touchscreen laptops).
4. **Keyboard.** A **`visualViewport`** listener writes a `--kb` custom property
   and the **drawer container shrinks from the bottom** (`bottom:
   calc(env(safe-area-inset-bottom) + var(--kb))`) — **not** a composer-only
   `translateY`, which desyncs the message scroller and hides the just-sent
   message. `100dvh` is for chrome only; it is blind to the iOS keyboard.
5. **Safe-area.** **`viewport-fit=cover`** + `env(safe-area-inset-*)` via `max()`
   on fixed chrome. No `maximum-scale`/`user-scalable` (break pinch-zoom /
   accessibility); `interactive-widget` is an iOS no-op as of mid-2026.
6. `prefers-reduced-motion` gates the slide; `focus-visible` rings precede the
   tap-highlight suppression; five `mousedown` outside-close/select listeners move
   to `pointerdown` (iOS doesn't reliably synthesize `mousedown` on tap).

## Consequences

- Desktop above 900px is **byte-for-byte unchanged**; the iPad becomes usable on
  touch in both orientations.
- **038** renders its decision bubble *inside* this drawer and inherits the
  `inert` + `aria-live` wiring; **039** extends the unread toggle dot into the
  attention router; **040** reuses `viewport-fit=cover` (its insets feed the PWA
  shell).
- **Cost:** we own focus-return, `inert` toggling, scroll-lock, and the keyboard
  math by hand. Residual risk is mitigated by using `inert` over a hand-rolled
  focus-trap and by a **mandatory real-iPad re-smoke** (portrait + landscape) —
  a curl-200 cannot validate Safari compile/mount/keyboard behaviour.
- A future **native `<dialog>` + `showModal()` refactor** would delete most of
  the hand-rolled a11y code — recorded as a follow-up, **not** S8 scope.

## Alternatives rejected

- **`display:none`** — the bug; deletes the command surface.
- **Stacked reflow** — ruled out by the user; buries the composer below dashboard
  cards in portrait.
- **Native `<dialog>`+`showModal()`** — free focus-trap/inert/ESC/aria-modal, but
  deferred to avoid relocating the chat-form and re-validating htmx/SSE under
  deadline. The recommended next refactor.
- **Universal 44px mins / width-keyed breakpoint** — inflate the dense desktop, or
  mis-fire on a narrow desktop window and a wide-landscape iPad.
- **Composer-only `translateY` for the keyboard** — desyncs the `flex:1` message
  scroller; the sent message and reply stay hidden behind the keyboard.
- **`interactive-widget=resizes-content`** — unimplemented on iOS Safari.
- **Anchoring the drawer `top:52px;bottom:30px`** — desyncs from safe-area-grown
  bars in landscape.
