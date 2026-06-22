# 042 — iPad web polish & token-entry UX (S8 follow-ups)

> Deferred to **S9** from the Ticket 037 real-iPad re-smoke + the 040
> home-screen install. None of these block daily use; they are rough edges + a
> web-auth UX preference + two 040 PWA follow-ups (stale-shell cache #3,
> standalone status-bar overlap #5). Pairs with the S9 web track.

## What

Clean up the iPad web experience surfaced while re-smoking 037:

1. **Token entry UX** — today the token lives in `sessionStorage`, so the
   in-page token form re-prompts every new tab/session. Pick a direction:
   - **A. `localStorage`** — enter once per device, persists (keep a token layer).
   - **B. Drop the token on the tailnet** — the web only binds the Tailscale IP,
     so Tailscale already authenticates the device; skip the token for web
     requests (simplest UX, slight trust trade-off).
   - **C. One-time login screen** instead of a mid-send modal.
   _Leaning A (or B if we trust the tailnet). Decide in S9 planning._

2. **Remove dead header buttons** — the chat-rail `+ New` and `History`
   buttons (`landing.html`, pre-existing) have **no handler** — placeholders
   that confuse on touch. Remove (or wire up) them.

3. **PWA cache invalidation** — the 040 service worker **precaches
   `landing.css` cache-first** and only refreshes on a `CACHE_VERSION` bump.
   Several S8 deploys shipped without bumping it, so the iPad serves a **stale
   shell** (e.g. the `Open chat` drawer toggle showed in landscape, where the
   live CSS hides it). Fix: bump `CACHE_VERSION` on every shell/asset change
   (ideally automate from a build hash) and serve `landing.css` **network-first**
   so CSS never goes stale. _Really a 040 follow-up; grouped here._

4. **Composer + on-screen-keyboard layout** — re-smoke pic showed an odd
   composer/terminal-bar gap with the keyboard up. **Verify** whether it's a
   real `--kb` / safe-area layout bug in 037 or just the stale cache (#3); fix
   if real. Carries the remaining **037 keyboard-visibility re-smoke**
   (sent message + reply stay above the keyboard).

5. **Standalone status-bar overlap** — in the **installed PWA** the iOS status
   bar (clock / VPN / battery) overlaps the `.top-bar` (the brand sits under the
   clock). Surfaced by the 040 home-screen install. Cause: 040 sets
   `apple-mobile-web-app-status-bar-style: black-translucent`, so the web view
   extends under the status bar, while `.top-bar` doesn't clear
   `env(safe-area-inset-top)` in the standalone context (037's safe-area padding
   is present but insufficient here — verify on-device). Fix: either drop to
   `status-bar-style: default` (the status bar gets its own opaque strip, no
   overlap) **or** keep edge-to-edge and add a `padding-top:
   env(safe-area-inset-top)` to `.top-bar` that actually applies when standalone.
   Needs on-device debugging (Safari remote inspector). _A 040 follow-up;
   grouped here._

## Why

037 made the iPad usable (drawer + send + keyboard handling shipped and
deployed); these are the polish items that make it *pleasant* and stop the PWA
serving a stale UI. Cheap, high-touch wins for the daily-driver goal.

## Non-goals

- New shell/layout redesign (037 owns the responsive shell).
- Web Push (041).
- Reworking the command/decision protocol.

## Notes / open

- Sprint: **2026-Q2-S9** (next). Confirm split vs. single ticket at S9 planning
  (the token-UX item may deserve its own ticket; #3 may fold into 040).
- Depends on nothing; #3 touches `service-worker.js` (040), the rest touch
  `landing.html` / `landing.css`.
- **Trivial unblock available anytime:** bumping `CACHE_VERSION` alone clears the
  stale-shell symptom without the rest of the work.
