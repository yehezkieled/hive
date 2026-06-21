# 037 — Responsive / touch shell (iPad daily driver)

> S8 foundation. The device is unusable on touch without this — do it first.
> The worst sub-bug (Enter-to-send) already shipped as a hotfix (PR #198).

## What

Make the existing web surface usable on an iPad in both orientations: fix the
`@media (max-width:900px){.chat-rail{display:none}}` rule that hides the whole
chat rail (and thus the command composer) at iPad-portrait width; raise
interactive targets to ≥44px; gate hover-only affordances behind
`(hover:hover)`; add `viewport-fit=cover` + safe-area-inset padding; handle the
on-screen keyboard (visualViewport) so the composer isn't covered; give the
drag-resizer a touch path. Fold in the Enter-to-send fix.

## Why

The web is already a working command + approval surface, but a web-primary iPad
daily driver is impossible if the composer disappears in portrait and mouse-only
interactions don't fire on touch. This is table-stakes plumbing every other S8
ticket rides on. (Competitor scan rank #6; survey flagged the 900px breakpoint.)

## Acceptance

- Chat rail + composer reachable at iPad-portrait width (drawer/toggle or
  reflow), not `display:none`.
- All interactive elements (approval buttons, chips, composer controls) ≥44px
  touch target with `:active` feedback.
- Hover-only states gated by `(hover:hover)`; tap equivalents where needed.
- `viewport-fit=cover` + safe-area insets on fixed chrome.
- On-screen keyboard does not cover the composer.
- Enter-to-send works on iPad (hotfix folded in); Send button works on touch.
- Deployed re-smoke on an **actual iPad**, portrait + landscape.

## Non-goals

- New observability widgets or chart redesigns.
- PWA install / push (040 / 041).
- Gesture shortcuts (swipe/long-press) — later polish.

## Notes

Touches `src/hive/web/static/landing.css`, `dashboard.css`, templates, and
dashboard JSX. Behaviour change in live web → deployed re-smoke required (Safari
must download/compile/mount; curl-200 is insufficient). Shares files with
038–040 → rebase order matters.
