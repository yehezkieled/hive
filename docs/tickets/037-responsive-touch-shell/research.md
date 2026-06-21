# Research — Ticket 037: Responsive / touch shell

What we found. Code claims carry `file:line`; platform claims are
current-iPadOS (mid-2026, verified against WebKit/MDN/web.dev). Produced by a
5-lens parallel audit (CSS / JS / tap+hover / iOS pitfalls / drawer pattern),
then stress-tested by a 3-lens adversarial pass — the §6 findings are what that
pass caught and changed.

> **Surface.** Everything lives in `src/hive/web/templates/landing.html` (1360
> lines, inline `<script>`) + `src/hive/web/static/landing.css` (1425 lines).
> Vanilla CSS + plain JS + htmx 2.0.4, **no build step**. The React `/dashboard`
> grid is a separate surface and is **out of scope**.

## Q1 — What breaks at iPad-portrait width (768px)?

- **The bug.** `landing.css:1423-1425` — `@media (max-width:900px){.chat-rail{display:none}}`
  deletes the **entire** chat column. The chat rail (`landing.html:53-133`,
  `<aside class="chat-rail">`) holds both the message list (`:69-90`) **and** the
  `/api/command` composer form (`:91-132`). Below 900px — iPad portrait is 768px —
  the only way to command or approve vanishes; the page is read-only.
- **Base layout** (`landing.css:238-248`): `.chat-rail` is `width:364px;
  flex-shrink:0` inside `.main-row` (`:56`, `display:flex`). The base flex model
  assumes a wide desktop and has **no narrow fallback** other than the
  `display:none`. `.shell` is `overflow:hidden` (`:54`) so any horizontal overflow
  is silently clipped — including controls you need.
- **Top bar overflow** (`landing.css:89-99, 100-107, 131-137, 168-174`): a single
  non-wrapping flex row — brand fixed **220px** + 4 tabs + health-pill + 2 bells +
  terminal-btn. At 768px the brand alone eats 220px; the row can overflow or
  squash, and there's no media query to relabel/hide low-value chrome or make
  room for a drawer toggle.

## Q2 — Minimal CSS to make `.chat-rail` a left slide-over drawer

At `<=900px`: take the rail out of flex flow and overlay it.
`position:fixed; inset:0; width:min(364px,86vw); z-index:40;
transform:translateX(-100%); transition:transform .25s`, revealed by a
shell-level `.drawer-open` class flipping `translateX(0)`. `transform` never
reflows, so `.content` keeps full width. **Anchor `inset:0`, not
`top:52px;bottom:30px`** — see §6, safe-area-grown bars would otherwise open a
seam. Desktop (`>900px`) keeps the existing static two-column flex untouched.

## Q3 — Tap-target sizing strategy: universal vs `pointer:coarse`

Every interactive control is under the 44px minimum (full inventory in §4).
**Use one `@media (pointer:coarse)` block, not a width breakpoint and not
universal mins.** Rationale: the dense desktop sizing is the product's identity
and must stay pixel-identical; `pointer:coarse` keys on the *input device*, so a
trackpad (`pointer:fine`) skips the bump and a landscape iPad (which can exceed
900px) still gets it. A width breakpoint false-positives a narrow **desktop**
window and false-negatives a wide-landscape iPad — wrong both ways.

## Q4 — Which `:hover` lifts get "stuck" on touch?

~18 rules do `transform: translateY` / `box-shadow` lifts that latch after a tap
on touch (no `mouseleave` fires): `.tab` (`:152`), `.bell` (`:200`),
`.terminal-btn` (`:234`), `.chat-rail__resizer` (`:260`), `.btn` (`:299`),
`.participant` (`:335`), `.mode-req__btn` (`:557,563`), `.composer__chip`
(`:645`), `.composer__chip--help` (`:658`), `.composer__send` (`:691`),
`.composer__attach` (`:710`), help-popup rows (`:787,819`), `.tasks-chip`
(`:1006`), `.maestro-card` (`:1048-1051`, the worst, `-2px`), `.btn-sm` (`:1154`),
`.vault-card` (`:1175`), idle-strip (`:1321-1324`), plus inline lifts at
`:1277-1278`. **Fix:** move them all into one `@media (hover:hover)` block; keep
`:active` press-feedback in the base. Use `(hover:hover)`, **not**
`(hover:hover) and (pointer:fine)` — the latter kills hover on touchscreen
laptops, a real regression.

## Q5 — Drag-resizer on touch

`setupRailResizer` (`landing.html:684`) binds `mousedown/mousemove/mouseup` only
— dead on touch, and the 6px strip (`landing.css:249-261`) is a sub-44px
tap-eater. **A fixed-overlay drawer has no neighbour column to resize**, so the
resizer is meaningless in portrait. Decision: early-return the handler under
`matchMedia` narrow, **clear the saved inline width** (`:691`, it's inline so it
outranks the drawer width), and `display:none` the handle at `<=900px`. Keep the
mouse path above 900px. **No** Pointer-Events port — out of scope.

## Q6 — Is the Enter-to-send hotfix complete?

Yes. `landing.html:645-660`: `submitComposer()` uses `requestSubmit()` with a
direct-call fallback for old iPadOS, and the keydown guard ignores
`isComposing`/`keyCode 229` (predictive-text Return) and respects Shift-Enter.
This is the PR #198 hotfix. **Preserve `640-660` byte-for-byte** — it's the
regression surface; the resizer/keyboard IIFEs sit near it but must not touch it.

## Q7–Q8 — iPadOS keyboard + viewport (platform facts)

- **The trap.** On iOS/iPadOS Safari the soft keyboard does **not** shrink the
  *layout* viewport — only the *visual* viewport shrinks and the layout viewport
  slides up. `100dvh`, `100%`, `-webkit-fill-available` all reference the layout
  viewport, so `dvh` tracks browser-chrome collapse but is **blind to the
  keyboard**. (Opposite of Android, where the keyboard *does* resize `dvh` — so
  "just use `h-dvh`" is wrong for iOS.) A bottom composer therefore gets covered.
- **Strategy.** Keep `height:100dvh` for the shell (chrome only). Drive the
  keyboard with the **`visualViewport` API**: a listener writes
  `--kb = max(0, innerHeight - vv.height - vv.offsetTop)` on `resize`/`scroll`
  (via `requestAnimationFrame`). **Do not** rely on
  `interactive-widget=resizes-content` — WebKit/iOS Safari still does not
  implement it as of June 2026 (Chrome/Firefox only).
- **Viewport meta** (`landing.html:5`, today `width=device-width, initial-scale=1`):
  → `width=device-width, initial-scale=1, viewport-fit=cover`. **No**
  `maximum-scale`/`user-scalable=no` (breaks pinch-zoom / WCAG, and iOS ignores
  them anyway).
- **Safe-area.** `viewport-fit=cover` is the switch that makes
  `env(safe-area-inset-*)` return nonzero. Map: top bar →
  `padding-top: max(8px, env(safe-area-inset-top))`; composer →
  `padding-bottom: max(12px, env(safe-area-inset-bottom))`; drawer →
  `padding-left: env(safe-area-inset-left)` (landscape). The `max()` idiom is
  safe unguarded on the iOS 15+ baseline; `constant()` is dead.
- **Keyboard × safe-area:** when the keyboard is up the home indicator hides, so
  the bottom inset goes to 0. Keep the keyboard offset (`--kb`) and the
  safe-area padding as **separate additive layers** so they never double-count.

## Q9–Q10 — Body-scroll-lock & focus containment (platform facts)

- **Scroll-lock.** `overflow:hidden` on `<body>` does **not** stop iOS
  background touch-scroll, and even a modal `<dialog>` doesn't lock page scroll
  on iOS. **But** this page is `.shell{overflow:hidden}` (`:54`) with the real
  scroller being `.content-inner{overflow:auto}` (`:75`) — so `<body>` likely
  doesn't scroll at all. **Lock the real scroller:** `.content-inner{overflow:hidden}`
  while the drawer is open (verify on-device); keep the `position:fixed` +
  saved-`scrollY` + `width:100%` + `scrollTo` body fallback only if the body can
  scroll in standalone-PWA/landscape.
- **Focus.** Without a native `<dialog>`, a scrim is **paint-only** — it does not
  remove DOM from the tab order or AX tree. We hand-build the containment with
  the `inert` property (Safari 15.5+, on the iPad baseline). Move-focus-in /
  return-on-close + `inert` is sufficient; a hand-rolled focus-trap is overkill.

## §6 — What the adversarial pass caught (8 critical fixes)

The first design passed the desktop-regression lens (sound) but **failed** the
iOS-keyboard and a11y lenses. Folded into the final design:

1. **Composer-only `translateY` hides what you send.** Lifting just `.composer`
   leaves the `flex:1` message scroller (`:351`) at full height, so the sent
   message + the agent reply land behind the keyboard, and the auto-scroll
   (`landing.html:355,379,437`) targets a bottom that's off-screen. → **Shrink the
   drawer container from the bottom** (`bottom: calc(env(safe-area-inset-bottom)
   + var(--kb))`) so the scroller actually contracts and `overflow:auto` keeps the
   newest bubble visible. Resize the box, don't transform a child.
2. **Closed drawer leaves controls focusable.** Closed = `translateX(-100%)`
   (off-screen, **not** `display:none`/`inert`), so its composer **and the
   SSE-injected Allow/Deny approval buttons** (`landing.html:359,419`) stay
   keyboard-focusable and VoiceOver-reachable while invisible. → set the rail
   `inert` when closed.
3. **Open drawer doesn't contain focus.** Tab from the composer lands on
   dashboard cards behind the scrim. → set `.top-bar` + `.content` `inert` on
   open; clear on close.
4. **No focus move/return.** → focus the **close-x** on open (not the textarea —
   auto-focusing it pops the iOS keyboard prematurely), restore focus to the
   toggle on close, via a single close code-path shared by scrim/ESC/close-x.
5. **`mousedown` outside-close fails on iOS.** The 5 existing outside-close /
   autocomplete-select handlers (`landing.html:771,834,968,1089,1210`) use
   `mousedown`, which iOS does not reliably synthesize on tap → backdrop-tap
   dismiss and suggestion-pick silently fail. → swap to `pointerdown`.
6. **SSE decisions rot off-screen.** A `mode_request`/`vault_request` arriving
   while the drawer is shut has **zero** attention signal (no `aria-live` exists
   anywhere). → add a visually-hidden `aria-live="assertive"` node in the shell;
   announce the requester; set an unread dot + `aria-label` on the toggle; re-run
   scroll-to-bottom on open. **This seeds Ticket 039.**
7. **Safe-area desyncs hardcoded anchors.** `top:52px/bottom:30px` breaks when
   safe-area grows the bars in landscape. → anchor the drawer/scrim `inset:0`.
8. **Wrong scroll-lock target.** Locking `<body>` is a no-op here; lock
   `.content-inner` (Q9).

## Decision-record scope (Q11)

The responsive-shell contract (drawer + `pointer:coarse` sizing +
`visualViewport` keyboard + `viewport-fit` safe-area + `inert`/`aria-live`
machinery) is **ADR-worthy** — tickets 038 (renders its decision bubble *inside*
this drawer), 039 (extends the toggle dot), and 040 (reuses `viewport-fit cover`)
all build on it. → **ADR 0022** (provisional; re-check at ship — numbers race
across worktrees). No `CONTEXT.md` term (drawer/scrim/tap-target are generic web
vocabulary); no README/DEPLOYMENT/ARCHITECTURE change.
