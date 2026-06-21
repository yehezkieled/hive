# Questions — Ticket 040: PWA install (home-screen app)

The unknowns going in. `research.md` answers each — code questions carry
`file:line` evidence; platform questions are current-iPadOS (mid-2026, verified
against WebKit/web.dev/MDN).

## Resolved before research (grill, with the user)

- **Q0a. Secure context — how do we serve the dashboard over HTTPS?** A service
  worker (and therefore PWA install + offline + 041 push) only runs in a *secure
  context* (`https://` or `http://localhost`); the iPad reaches Hive at plain
  `http://100.79.194.84:8080`. → **RESOLVED: `tailscale serve`** (Option A,
  chosen by the user) — Tailscale terminates TLS and reverse-proxies to the local
  uvicorn, giving `https://<node>.<tailnet>.ts.net/` with a real cert, no new
  public port, no app-code change. Rejected: caddy/nginx proxy, uvicorn-native
  TLS, localhost+SSH tunnel (see `design.md`).
- **Q0b. Is the HTTPS enablement in 040's scope, or a separate prerequisite
  ticket?** → **RESOLVED: in 040** (chosen by the user). The install can't be
  verified without it, so 040 is **cross-cutting** — it edits `docs/DEPLOYMENT.md`
  and carries a one-time ops step.
- **Q0c. How ambitious is the offline shell?** → **RESOLVED: minimal** (chosen by
  the user) — cache the page shell + `/static/*` + the pinned CDN deps; show a
  "reconnecting" fallback when `/api/*` is unreachable; **do not** cache live fleet
  data. Matches the ticket non-goal "only a shell + cached static assets."
- **Q0d. App icon — reuse or generate?** → **RESOLVED: generate a placeholder**
  (chosen by the user) — no logo/icon exists anywhere in the repo; ship a simple
  honeycomb-hexagon mark in the brand palette, swappable later.

## Secure context / deployment

- **Q1.** Exact `tailscale serve` invocation + prerequisites on this VPS (MagicDNS
  + HTTPS-certs toggle in the tailnet admin)? What host:port does uvicorn bind,
  and does the app re-bind to loopback or keep the tailnet IP once TLS fronts it?
- **Q2.** Does any app code assume `http://`/the tailnet IP (absolute URLs,
  redirects, cookie `Secure`/SameSite, CORS, SSE origin)? Anything that breaks
  when the origin becomes `https://<node>.ts.net`?

## Manifest / service worker (code)

- **Q3.** Where do the manifest and service worker get *served from*? (SW must be
  **root-scoped** to control `/` **and** `/dashboard` — what is the existing
  root-route pattern to copy, and what media types / cache headers does each need?)
- **Q4.** What exactly should the SW cache, and with which strategy per request
  class — navigations, `/static/*`, cross-origin CDN (React/Babel/htmx/fonts),
  and `/api/*` + `/sse/*`? Which must **never** be cached?
- **Q5.** SW lifecycle: how do we avoid serving stale JS forever (cache
  versioning, `skipWaiting`/`clients.claim`, deleting old caches, `no-cache` on the
  SW file itself)?

## Templates / head (code)

- **Q6.** Is there a shared base template, or must the manifest link + apple meta +
  SW-registration script be added to **both** `landing.html` and `dashboard.html`?
- **Q7.** Which exact `<meta>`/`<link>` tags does current iPadOS Safari need for a
  proper standalone install (apple-touch-icon, `apple-mobile-web-app-capable`,
  status-bar-style, title, `theme-color`, favicon), and how do they interact with
  037's `viewport-fit=cover`?
- **Q8.** Coordination with 037/038 — all four S8 tickets edit the same `<head>`;
  what's the rebase order and the collision surface?

## Icons / assets (code + packaging)

- **Q9.** What icon set does iPadOS actually use (apple-touch-icon size; PNG vs
  maskable; favicon), and how are the placeholders generated without adding a
  runtime image dependency?
- **Q10.** Are new files under `src/hive/web/static` shipped by the current deploy
  (source-tree run) or do they need `[tool.setuptools.package-data]`?

## Tests / verification

- **Q11.** What's the existing web-test pattern (FastAPI `TestClient`), and what
  *can* a unit test prove (routes + markup) vs. what is **only** confirmable on a
  real iPad over HTTPS (SW registration, Add-to-Home-Screen standalone, offline
  shell, status bar)?

## Decision-record scope

- **Q12.** Is "serve the dashboard over HTTPS via Tailscale to unlock the
  secure-context PWA features" ADR-worthy (041 push builds on it; it changes how
  every device reaches Hive)? If so, the next free ADR number (provisionally
  **0023** — re-check at ship; numbers race across worktrees).
