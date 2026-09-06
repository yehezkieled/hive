# 040 — PWA install (home-screen app)

> Makes Hive a home-screen app, not a Safari tab — core to "daily driver." And
> the prerequisite for iOS Web Push (041), which only fires for an installed PWA.

## What

Make the web app installable on iPadOS: a web app manifest (name, icons,
`display: standalone`, `start_url`, theme), a service worker caching an offline
shell (static assets + last-known view), `apple-touch-icon` +
`apple-mobile-web-app-capable` / `-status-bar-style` meta tags, and the icon
assets (192/512 PNG, favicon).

## Why

Three independent peers (Devin, Cursor, vibe-kanban) chose PWA over an App Store
binary for exactly Hive's single-user, self-hosted, ship-fast situation — direct
validation. It's also the hard dependency for Web Push on iOS 16.4+ (push only
fires for a Home-Screen-installed PWA). Confirmed missing: no manifest / service
worker / apple meta in `src/hive/web/static` today. (Competitor scan rank #4.)

## Acceptance

- Web app manifest served + linked; app installs to the iPad home screen with a
  proper icon and standalone (no Safari chrome) launch.
- Service worker registered; an offline shell renders when the network is down.
- `apple-touch-icon` + `apple-mobile-web-app` meta present.
- Verified **installed on an actual iPad** (home-screen launch, standalone mode).

## Non-goals

- Web Push / VAPID (041) — separate ticket, blocked on this.
- Offline command queue / IndexedDB sync — later.
- Full offline functionality (only a shell + cached static assets).

## Notes

New assets under `src/hive/web/static` + manifest/SW routes in `app.py` + head
tags in `landing.html` / `dashboard.html`. Gates 041.
