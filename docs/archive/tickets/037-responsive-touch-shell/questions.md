# Questions — Ticket 037: Responsive / touch shell

The unknowns going in. `research.md` answers each (with file:line evidence
for the code questions, and current-iPadOS evidence for the platform ones).

## Resolved before research

- **Q0. Portrait layout for the chat rail — drawer, stacked reflow, or
  something else?** → **RESOLVED: left slide-over drawer** (Option A, chosen by
  the user). The rail must not be `display:none` and must not push dashboard
  content. Research designs the drawer, not the choice.

## Layout / CSS

- **Q1.** Exactly which CSS rules break at iPad-portrait width (768px)? Is the
  `.chat-rail{display:none}` at `landing.css:1423` the only blocker, or do
  `.shell`/`.main-row`/`.content` widths force horizontal overflow too?
- **Q2.** What is the minimal CSS to turn `.chat-rail` into a left slide-over
  drawer at `<=900px` while leaving the desktop two-column layout untouched
  (transform, position, z-index, backdrop, where the toggle/close attach)?
- **Q3.** Tap-target sizing: do we raise everything to ≥44px **universally**, or
  bump only under `@media (pointer:coarse)` so the deliberately dense desktop
  layout keeps its density? Which one actually satisfies acceptance without
  regressing desktop?

## Touch / interaction

- **Q4.** Which `:hover` lift effects (~20 `transform/box-shadow` rules) get
  "stuck" after a tap on touch, and is `@media (hover:hover)` gating the right
  fix for all of them?
- **Q5.** Does the drag-resizer (`setupRailResizer`, `landing.html:684`, mouse
  events only) need a touch/pointer path, or is it moot once the rail is a
  drawer in portrait? Keep, add Pointer Events, or disable in the drawer state?
- **Q6.** Is the existing Enter-to-send hotfix (`landing.html:640–660`, PR #198)
  complete and correct, and how do we fold it in without regressing it?

## iPadOS / Safari platform

- **Q7.** On-screen keyboard: does the bottom-pinned composer stay visible when
  the soft keyboard opens? `visualViewport` API vs viewport
  `interactive-widget=resizes-content` — which works on current iPadOS, and what
  is the JS/meta shape?
- **Q8.** Exact viewport meta string: add `viewport-fit=cover`; add
  `interactive-widget`? Which chrome (top bar, composer) needs which
  `env(safe-area-inset-*)`, and is `height:100dvh` enough or is a
  `-webkit-fill-available`/fallback needed?
- **Q9.** iOS body-scroll-lock: `overflow:hidden` on `body` does not stop iOS
  touch scroll behind an overlay — what is the robust lock for the open drawer?
- **Q10.** Does the drawer need a full focus-trap, or is move-focus-in /
  return-on-close + background `inert`/`aria-hidden` enough for this case?

## Decision-record scope

- **Q11.** Is the responsive-shell contract (drawer + sizing strategy + keyboard
  strategy + safe-area) worth an ADR so tickets 038–040 build on the same
  conventions? If so, the next free ADR number (provisionally **0022** — re-check
  at ship; numbers race across worktrees).
