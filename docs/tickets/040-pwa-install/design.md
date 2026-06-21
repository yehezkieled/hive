# Design — Ticket 040: PWA install (home-screen app)

Chosen approach for making Hive installable on the iPad home screen. The pivot
isn't the manifest — it's the **secure context**: a service worker only runs over
HTTPS, and Hive is served plain-HTTP today, so 040 first puts the dashboard
behind **`tailscale serve` HTTPS**, then adds the standard PWA artifacts (manifest
+ root-scoped service worker + apple meta + generated icons). Decisions settled in
the design grill with the user; evidence in `research.md`. **No app logic changes
— only deployment, a few small routes, static files, and `<head>` tags.**

Side effects shipped with this design: **[ADR 0023](../../adr/0023-https-via-tailscale-serve-for-pwa.md)**
(serve the dashboard over HTTPS via Tailscale to unlock the secure-context PWA
line). Cross-cutting **`docs/DEPLOYMENT.md`** edit declared in `plan.md`. No
`CONTEXT.md` term (PWA/service-worker/secure-context are generic web vocabulary).

## Decision summary

| # | Decision | Rationale (short) |
|---|----------|-------------------|
| **D1** | **Serve the dashboard over HTTPS via `tailscale serve`** (TLS-terminate + reverse-proxy to the local uvicorn). Re-bind uvicorn to **loopback** (`HIVE_WEB_HOST=127.0.0.1`) and front it **exclusively** with `tailscale serve https / → http://127.0.0.1:8080`, giving a **single HTTPS origin** `https://<node>.tailfb3900.ts.net/`. **Not** caddy/nginx, uvicorn-native TLS, or localhost+tunnel. | A service worker needs a secure context; Hive is plain HTTP today (`__main__.py:406`, no `ssl_*`). Tailscale already runs → automatic managed cert, **tailnet-only** (not `funnel`), **zero Python change**. A single HTTPS origin keeps the installed app + its storage unambiguous (a dual HTTP-IP/HTTPS-host setup splits origins). → **ADR 0023.** |
| **D2** | **Manifest** at a root route `GET /manifest.webmanifest` (`application/manifest+json`): `name:"Hive"`, `short_name:"Hive"`, **`start_url:"/"`, `scope:"/"` (relative — host-agnostic)**, `display:"standalone"`, `theme_color:"#e0a726"`, `background_color:"#faf7ed"`, `icons:[192, 512, 512-maskable]`. | Relative `start_url`/`scope` survive the HTTP→HTTPS origin move. `standalone` drops Safari chrome. Palette from `landing.css:7-23` (honey on paper). |
| **D3** | **Service worker** at a root route `GET /service-worker.js` (`application/javascript`, **`Cache-Control: no-cache`**). Registered by a small inline `<script>` guarded by `if ('serviceWorker' in navigator)`. | A SW controls only at/below its own path — **root** is required to control `/` **and** `/dashboard` (a `/static/sw.js` couldn't). `no-cache` on the file lets updates propagate. |
| **D4** | **Caching = minimal shell, per request class** (table below): navigations **network-first** → `/offline.html` fallback; `/static/*` **stale-while-revalidate**; cross-origin CDN **cache-first** (opaque); **`/api/*` + `/sse/*` + POST → network-only, never cached.** Offline is guaranteed for the **landing** shell; React `/dashboard` offline is best-effort. | "last-known view" without serving stale-forever online; never cache live/auth'd/streaming responses. Matches the non-goal "only a shell + cached static assets" (user: minimal). |
| **D5** | **SW lifecycle:** a `CACHE_VERSION` namespaces caches; `install` precaches the shell + `/offline.html` + icons + `skipWaiting()`; `activate` deletes non-current caches + `clients.claim()`. Bump the version on any shell change. | Stops "stale JS forever" — the classic SW footgun. |
| **D6** | Add the **manifest link + apple meta + `theme-color` + favicon + SW-registration script to BOTH** `landing.html` and `dashboard.html` heads (no shared base). `apple-mobile-web-app-status-bar-style: black-translucent`. | Two standalone templates (`*.html:3-12`) — there's no base to edit once. `black-translucent` pairs with 037's `viewport-fit=cover` so content fills under the status bar. |
| **D7** | **Generate placeholder icons** — a honeycomb-hexagon "Hive" mark (honey `#e0a726` on paper `#faf7ed`). Author **one SVG**, rasterize dev-time to `icon-192.png`, `icon-512.png`, `icon-512-maskable.png` (safe-zone padded), `apple-touch-icon-180.png` (opaque, no alpha), `favicon.ico`. Commit the outputs + the SVG. | No logo exists in the repo. Committed PNG/ICO ⇒ **no runtime image dependency**; the SVG source makes a real-branding swap a one-file change later. |
| **D8** | **Do not** add `[tool.setuptools.package-data]`. | Prod runs from the source tree and existing `static/` assets already ship; package-data only matters for a wheel/non-editable install → optional hardening, kept out to minimise the change. |

## Why `tailscale serve`, not the alternatives

```
plain HTTP (today)        →  http://100.79.194.84:8080 — non-localhost → SW BLOCKED. the bug.
localhost + SSH tunnel    →  http://localhost works, but kills "just open it on the iPad". rejected.
uvicorn --ssl-keyfile     →  app terminates TLS; self-signed → iPad trust prompts; cert mgmt in-app. rejected.
caddy / nginx reverse-proxy→ works (auto-LE), but a second daemon to run + a Caddyfile to own. rejected.
tailscale serve (D1)      →  TLS-terminate on the daemon already running; managed cert; tailnet-only;
                            zero Python change; single https://<node>.ts.net origin. CHOSEN.
                            Cost: a one-time admin toggle (MagicDNS + HTTPS certs) + the access URL moves.
```

## Service-worker caching — the core mechanism (D4)

```
            fetch event
                │
   ┌────────────┼─────────────────────────────────────────────┐
   │            │                                              │
 /api/* , /sse/* , POST        navigations (/, /dashboard)   /static/*        cross-origin CDN
   │                                  │                         │             (react/babel/htmx/fonts)
 NETWORK-ONLY                   NETWORK-FIRST              STALE-WHILE-       CACHE-FIRST (opaque,
 (SW bypasses; never cache —    → cache the response        REVALIDATE        runtime-cached on 1st hit)
  live, auth'd, streaming)      → offline: last-cached      (?v=N busts)      ⚠ Babel ~3MB — landing
                                  page, else /offline.html                       shell guaranteed; dash
                                                                                  offline = best-effort
   activate: delete caches != CACHE_VERSION  ·  install: precache shell + /offline.html + icons, skipWaiting
```

Why never the simple "cache everything": caching `/api/*` serves **stale fleet
state**; caching `/sse/notifications` (a `text/event-stream`) **buffers a stream
that never ends**; caching authed POSTs is a correctness/security hole. The SW
**must** pass those straight to the network.

## The origin move (D1) — what changes for the user

```
 BEFORE:  iPad ─ http://100.79.194.84:8080/ ─────────────▶ uvicorn (bound 100.79.194.84:8080)
 AFTER:   iPad ─ https://<node>.tailfb3900.ts.net/ ─ TLS ─▶ tailscale serve ─▶ uvicorn (bound 127.0.0.1:8080)
                                                                     │
                            real managed cert, tailnet-only, secure context → SW registers ✓
```

One-time ops (the user, on the VPS): enable **MagicDNS + HTTPS certificates** in
the tailnet admin; `tailscale serve` the dashboard; set `HIVE_WEB_HOST=127.0.0.1`;
restart `hive.service`. Documented in `DEPLOYMENT.md`; the old HTTP-IP smoke URL
is replaced by the HTTPS hostname.

## Residual risks (carried into the plan's verification)

- **`tailscale serve` syntax + cert provisioning** vary by Tailscale version and
  need the admin HTTPS toggle — confirm the exact invocation on the VPS at deploy;
  the cert can take a minute to issue on first request.
- **Re-bind to loopback** means any device currently using `http://100.79.194.84:
  8080` must switch to the HTTPS hostname — intended, but the one live behaviour
  change → re-smoke from another tailnet device too, not just the iPad.
- **SW staleness** — a missed `CACHE_VERSION` bump pins users on old assets;
  `no-cache` on the SW file + version-delete on `activate` are the guards.
- **Opaque CDN cache bloat** — Babel-standalone (~3 MB) cached opaque; acceptable
  for pinned versions, but don't precache it at `install` (runtime-cache only) so
  the landing shell install stays light.
- **Head collision with 037** — both edit the `<head>`; 040 rebases after 037.
- **DoD is device-only** — SW registration, standalone launch, and the offline
  shell are **only** confirmable in real iPad Safari over HTTPS; a curl-200 cannot
  validate it. Mandatory real-iPad re-smoke.
