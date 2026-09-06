# Plan — Ticket 043: standalone PWA status-bar overlap  (issue #221)

**Lane:** direct (one CSS rule, one PR). **Sprint:** 2026-Q2-S9.

Reserve the missing **top** safe-area inset on the shared `.top-bar` rule so the
installed (standalone) PWA's chrome clears the iOS status bar. Full reasoning in
`research.md` / `design.md`.

## Files this Ticket creates / modifies

| Path | Op | Step |
|------|----|------|
| `src/hive/web/static/landing.css` | modify | In the `.top-bar` rule (~line 96): change `height: 52px` → `height: calc(52px + max(0px, env(safe-area-inset-top)))`, and add `padding-top: max(0px, env(safe-area-inset-top));` beside the existing `padding-left`/`padding-right`. |

That is the whole change — two lines in one rule. No template, meta, JS, or
Python edits. `_pwa_head.html` keeps `black-translucent` (design rejects the
`status-bar-style: default` alternative).

## Verification

- **On-device re-smoke (the real gate):** installed PWA on an actual iPad,
  home-screen launch (standalone), **portrait + landscape** — the status bar no
  longer overlaps the brand / tabs / pills / bells / terminal button.
- **Regression:** desktop and in-Safari (non-standalone) iPad layouts visually
  unchanged (the `max(0px, env(...))` guard zeroes off-device).
- No Python touched → no new unit tests; `ruff`/`pytest` unaffected. (CSS has no
  CI gate; the build PR rides the existing suite green.)

## Out of scope

- The other 042 iPad-polish items (token-entry UX, dead header buttons, stale
  shell cache, keyboard layout).
- Any shell/layout redesign (037 owns the responsive shell).
- Web Push (041).
- The `status-bar-style: default` alternative (rejected in `design.md`).

## Cross-cutting impact

- **None.** No `CONTEXT.md` term, no ADR (regression fix inside 040's
  `black-translucent` / ADR 0023's HTTPS origin), no `DEPLOYMENT.md` /
  `README.md` change.

## Build handoff

Direct lane: one branch `ticket-043/status-bar-inset` → one PR that closes
**#221** after the on-device iPad re-smoke passes. `run-ticket` ends here (plan
shipped); the build is the next step.
