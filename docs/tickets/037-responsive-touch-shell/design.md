# Design — Ticket 037: Responsive / touch shell

Chosen approach for making the Hive landing page an iPad daily driver: the chat
rail becomes a **class-toggled fixed-overlay slide-over drawer** at narrow width,
with the dialog machinery hand-built (the page can't adopt native `<dialog>`
without relocating the hotfix-bearing composer and re-validating htmx/SSE).
Decisions settled in the design grill + a 3-lens adversarial pass; evidence in
`research.md`. **Desktop above 900px stays byte-for-byte unchanged.**

Side effects shipped with this design: **[ADR 0022](../../adr/0022-responsive-touch-shell-contract.md)**
(the touch-shell contract for 038–040). No `CONTEXT.md` term needed.

## Decision summary

| # | Decision | Rationale (short) |
|---|----------|-------------------|
| **D1** | Replace `landing.css:1423-1425` (`.chat-rail{display:none}`) with a **class-toggled fixed-overlay slide-over drawer** at `<=900px`, anchored `inset:0`. Desktop two-column flex `>900px` untouched. **Not** native `<dialog>`. | `display:none` deletes the only command/approval input. `transform:translateX` never reflows → content keeps full width. Class-toggling keeps the hotfix-bearing `<form>` byte-intact. `inset:0` decouples the drawer from safe-area-grown bars (no pokeable seam). |
| **D2** | New narrow-only **`.drawer-scrim`** (z-index 39, `inset:0`, `pointerdown` to dismiss) + **scroll-lock on the real scroller**. Don't reuse `.backdrop` (`:79`). | `.backdrop` is z-0, `pointer-events:none`, inside `.content` — can't receive taps. iPad keyboard has no Esc, so scrim-tap is the primary dismiss. The real scroller is `.content-inner` (`:75`), not `<body>`. |
| **D3** | Bump controls to **44px in one `@media (pointer:coarse)` block** (not width, not universal); grow tiny glyphs via an inset `::before` hit-box. | `pointer:coarse` keys on input device → desktop stays pixel-identical, landscape iPad still covered, trackpad skips. A width breakpoint mis-fires both ways. |
| **D4** | Move the **~18 hover lift rules into one `@media (hover:hover)` block** (drop `pointer:fine`); keep `:active` in the base. | Hover latches after a tap on touch. `(hover:hover)` already excludes touch-only; adding `pointer:fine` would kill hover on touchscreen laptops. `:active` gives press feedback on both. |
| **D5** | Add **`viewport-fit=cover`** to the meta + **`env(safe-area-inset-*)` via `max()`** on top-bar / terminal-bar / composer / drawer. No `maximum-scale`/`user-scalable`; `interactive-widget` not load-bearing. | `viewport-fit=cover` is the switch that makes the insets nonzero (also needed by 040). `max()` guards the inset; the disallowed tokens break pinch-zoom and are iOS no-ops anyway. |
| **D6** | A **`visualViewport` listener writes `--kb`**; the **drawer container's `bottom` = `calc(env(safe-area-inset-bottom) + var(--kb))`**. Do **not** translate the composer alone. `100dvh` is for chrome only. | iOS shrinks only the visual viewport, so `dvh` is blind to the keyboard. Shrinking the fixed box lets the `flex:1` scroller (`:351`) contract → the sent message + reply stay visible; a composer-only `translateY` desyncs the scroller and hides them. |
| **D7** | **Early-return `setupRailResizer`** under `matchMedia` narrow, **clear the saved inline width** on every narrow crossing, hide the 6px handle at `<=900px`. Keep the mouse path `>900px`. Preserve the **Enter-to-send hotfix `640-660` byte-for-byte**. No Pointer-Events upgrade. | A fixed-overlay drawer has no neighbour to resize; the inline saved width (`:691`) would fight the drawer width and must be *cleared*, not just unapplied. The 6px strip is a dead tap-eater. The hotfix is the regression surface. |
| **D8** | **Hand-build the dialog machinery:** `inert` the off-drawer world in **both** states; move focus to close-x on open / back to the toggle on close; add ARIA + a `sr-only` utility + an **`aria-live` node that announces SSE decisions even when the drawer is shut**. Gate the slide on `prefers-reduced-motion`. Add `focus-visible` rings. | Stop-condition: focus must not escape to hidden background. A scrim is paint-only. Closed = off-screen-but-focusable → its composer + SSE Allow/Deny buttons stay AX-reachable. SSE bubbles (`:359,419`) mutate the rail live with no signal today. `inert` gives containment without a hand-rolled trap. |
| **D9** | Swap the **5 `mousedown` outside-close/select listeners → `pointerdown`** (`landing.html:771,834,968,1089,1210`). | iOS synthesizes `click` on tap but **not** `mousedown` → tap-to-dismiss and autocomplete-pick silently fail. Several are the bell/gate approval popups the drawer shares chrome with. |

## Why a drawer, not the alternatives

```
display:none (the bug)   →  deletes the command surface. rejected.
stacked reflow           →  ruled out by the user; buries the composer below cards in portrait.
native <dialog>+showModal →  free focus-trap/inert/ESC/aria-modal, BUT needs relocating the
                            chat-form + re-validating htmx & SSE under deadline. Deferred —
                            the recommended NEXT refactor (would delete most of D8's hand code).
slide-over drawer (D1)   →  chat one tap away, overlays (doesn't push cards), composer pins
                            above the keyboard. Chosen. Cost: own the a11y machinery (D8).
```

## Keyboard handling — the core mechanism (D6)

```
iOS Safari, keyboard opens:
  ┌──────────────── layout viewport (100dvh) — does NOT shrink ────────────────┐
  │  top bar                                                                    │
  │  ┌─ drawer (position:fixed, inset:0) ─────────────┐                         │
  │  │  messages  (flex:1, overflow:auto)             │  ← contracts because    │
  │  │  ...                                           │     the BOX shrinks      │
  │  │  composer  (flex-shrink:0)                     │  ← rides up in flow      │
  │  └────────────────────────────────────────────────┘                         │
  │  bottom: calc(env(safe-area-inset-bottom) + var(--kb))  ← visual-viewport gap│
  ├═════════════════ soft keyboard (covers visual vp only) ════════════════════┤
  └─────────────────────────────────────────────────────────────────────────────┘
   --kb = max(0, window.innerHeight - visualViewport.height - visualViewport.offsetTop)

Wrong way (rejected): transform:translateY(-kb) on .composer only.
  → the input lifts, but .chat-rail__messages keeps full height; the sent message
    + agent reply stay behind the keyboard, and scrollTop=scrollHeight scrolls to a
    bottom that's off-screen. You can send but not see what you sent.
```

## Residual risks (carried into the plan's verification)

- **Top-bar fit at 768px** — brand 220px + 4 tabs + pills + 2 bells + terminal +
  a 44px toggle, under `.shell{overflow:hidden}`. Plan resolves by hiding
  `terminal-btn` + brand-meta at narrow; keeping both bells. (Open Q in `plan.md`.)
- **`visualViewport.offsetTop` over-subtraction** when Safari auto-scrolls — bind
  both `resize` + `scroll`, recompute after auto-scroll settles, re-smoke with the
  field near the drawer bottom.
- **Scroll-lock target** — confirm on-device whether `<body>` scrolls at all
  (likely not); if not, `overflow:hidden` on `.content-inner` suffices.
- **`inert` strand** — forgetting to clear `inert` freezes the UI; clear it in the
  shared teardown path tied to focus-return.
- All JS-rendered + keyboard behaviour is **only** verifiable in real Safari — a
  curl-200 cannot validate compile/mount/keyboard. Mandatory real-iPad re-smoke.
