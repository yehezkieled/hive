# Questions — Ticket 043: standalone PWA status-bar overlap

The unknowns going in. Answered in `research.md` with code refs.

1. **Where is `.top-bar` defined, and is the same rule shared by the landing
   page and the dashboard?** The PWA `start_url` decides which page is the
   installed app; the fix must cover whatever standalone renders.

2. **Did Ticket 037 actually add top safe-area padding to `.top-bar`?** The
   ticket assumes it did but "isn't taking effect" (gated to a media query, or
   the inset computes as 0). Confirm in code whether a `padding-top:
   env(safe-area-inset-top)` exists at all.

3. **What is the global `box-sizing`, and does `.top-bar` have a fixed
   height?** This decides whether adding `padding-top` grows the bar (pushes
   content below the status bar) or shrinks the content box (squishes content
   *under* the status bar — a non-fix).

4. **Does anything depend on the top bar being exactly 52px** (a hardcoded
   `top: 52px` offset, a `position: fixed` sibling pinned below it)? If so,
   growing the bar would shift those elements.

5. **Which of the two candidate fixes is right** — (#1) `status-bar-style:
   default` (opaque strip, lose edge-to-edge) vs (#2) keep `black-translucent`
   + reserve the top inset on `.top-bar` — and is the choice still gated on
   on-device debugging, or does the code already settle it?

6. **Will the fix leave desktop and in-Safari (non-standalone) layouts
   byte-identical**, as the acceptance criteria require?
