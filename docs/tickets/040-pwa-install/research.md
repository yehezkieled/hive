# Research — Ticket 040: PWA install (home-screen app)

What we found. Code claims carry `file:line`; platform claims are current-iPadOS
(mid-2026, verified against WebKit/web.dev/MDN). Produced by a 4-lens parallel
read-only audit of the web layer (serving/routes · templates/head · secure
context/deployment · tests/assets/packaging).

> **Surface.** The web app is **FastAPI + uvicorn**, vanilla — Jinja2 templates,
> `StaticFiles` mount, **no build step**. Two *standalone* full-page templates
> (`landing.html`, `dashboard.html`) with **no shared base**. The landing page is
> htmx + plain JS (the command/approval surface); `/dashboard` is React 18 +
> Babel-standalone compiled **in the browser** from CDN. New PWA artifacts drop
> in as static files + a few small routes — no pipeline to fight.

## Q0a / Q1 / Q2 — Secure context: HTTP today, the blocker, and the fix

- **The blocker (the finding that reshapes the ticket).** A service worker — and
  therefore PWA install, offline, and 041 push — only runs in a **secure
  context**: `https://` or `http://localhost`. Today the iPad reaches Hive at
  **`http://100.79.194.84:8080/`** (plain HTTP, non-loopback) → the SW **silently
  refuses to register**, and 040's DoD ("verified installed on an actual iPad,
  standalone") cannot pass as-is.
  - Evidence: `src/hive/config.py:103-104` — `WEB_PORT = HIVE_WEB_PORT (8080)`,
    `WEB_HOST = HIVE_WEB_HOST (default 127.0.0.1)`; production `.env` sets
    `HIVE_WEB_HOST=100.79.194.84`, `HIVE_WEB_PORT=8080`.
  - `src/hive/__main__.py:406` — `uvicorn.Config(web_app, host=WEB_HOST,
    port=WEB_PORT, …)` with **no `ssl_*` params** → plain HTTP only.
  - `docs/DEPLOYMENT.md:229-230, 293, 369` — every documented access + smoke URL
    is `http://100.79.194.84:8080/`.
  - Repo-wide search: **no** nginx/caddy/traefik, **no** `tailscale serve`/
    `funnel`, **no** TLS/cert references anywhere. Tailscale is used only for the
    IP bind.
- **The fix (chosen): `tailscale serve`.** `tailscaled` already runs on the VPS.
  Enabling MagicDNS + "HTTPS certificates" in the tailnet admin (one-time, user
  action) lets `tailscale serve` terminate TLS with a real Tailscale-managed
  Let's-Encrypt cert and reverse-proxy to the local uvicorn — exposing
  `https://<node>.tailfb3900.ts.net/` **tailnet-only** (not `funnel`/public). No
  Python change; no new listening port on the app.
  - Target end-state (Q1): **re-bind uvicorn to loopback** (`HIVE_WEB_HOST=
    127.0.0.1`) and serve **exclusively** through `tailscale serve https / →
    http://127.0.0.1:8080`, so there is a **single HTTPS origin**. Exact `serve`
    syntax varies by Tailscale version → confirm on the VPS at deploy.
- **App-code assumptions (Q2).** None block the switch: routes are all
  **relative** (`/static/...`, `/api/...`, template `href`/`src` are root-relative
  — `landing.html:10`, `dashboard.html:10-11,61-68`); the `require_token()` Bearer
  auth is origin-agnostic; SSE (`/sse/notifications`) is same-origin. The one
  behaviour change is **the access URL itself** (HTTP IP → HTTPS hostname) → a
  `DEPLOYMENT.md` edit + a new smoke URL. Keep every new PWA URL (`start_url`,
  manifest `href`, SW `register()`) **relative** so the app stays host-agnostic.

## Q3 — Where the manifest + service worker are served

- **Framework/pattern** (`src/hive/web/app.py`): `app = FastAPI(...)` (`:73`);
  `Jinja2Templates(directory=TEMPLATES_DIR)` (`:74`); `app.mount("/static",
  StaticFiles(directory=STATIC_DIR), name="static")` (`:75-76`). Root routes use
  `@app.get("/", response_class=HTMLResponse)` (`:476-493`).
- **Service-worker scope rule.** A SW controls only URLs **at or below its own
  path**. To control both `/` and `/dashboard` it must be served from the **root**
  — `GET /service-worker.js`, not `/static/...` (a `/static/sw.js` would only
  control `/static/*`). Copy the root-route pattern; return `FileResponse(STATIC_DIR
  / "service-worker.js", media_type="application/javascript")` **with
  `Cache-Control: no-cache`** so SW updates are picked up (Q5).
- **Manifest.** Scope-insensitive → either a root route `GET /manifest.webmanifest`
  (clean, conventional) returning `media_type="application/manifest+json"`, or a
  static file linked explicitly. We use the **root route** for symmetry with the SW.
- **No existing manifest/SW route** today (confirmed).

## Q4 — What the SW caches, per request class (minimal shell, Q0c)

| Request class | Strategy | Why |
|---|---|---|
| **Navigations** (`/`, `/dashboard`) | **network-first** → cache the response → offline: serve last-cached page, else `/offline.html` | "last-known view"; never block on a dead network, never serve a stale-forever page online. |
| **`/static/*`** (css, jsx, refresh.js, icons) | **stale-while-revalidate** (cache-first + background refresh) | versioned with `?v=N` (`dashboard.html:65-68`) so a bump busts naturally; instant offline boot. |
| **Cross-origin CDN** (unpkg React/Babel/htmx, Google Fonts) | **cache-first**, opaque (`no-cors`) responses, runtime-cached on first hit | pinned versions (`landing.html:11`, `dashboard.html:61-63`); lets the *landing* shell boot offline. ⚠ Babel-standalone is ~3 MB — see note. |
| **`/api/*`, `/sse/*`, `POST`** | **network-only — never cached, SW bypasses** | live data, Bearer-auth, and a streaming `text/event-stream` (`/sse/notifications`); caching them would serve stale/auth'd/over-buffered responses. |

> **Scope note (Q0c).** Offline is guaranteed for the **landing** shell (the
> command surface — htmx only, tiny). The React `/dashboard` offline-boot depends
> on the heavy Babel/React CDN cache and is **best-effort**, not a DoD line.

## Q5 — SW lifecycle (no stale-forever JS)

- A `CACHE_VERSION` const namespaces the caches; `install` precaches the shell +
  `/offline.html` + icons; `activate` **deletes caches** whose name ≠ the current
  version, then `clients.claim()`. `skipWaiting()` (on `install` or a message)
  promotes the new SW immediately. The SW file is served `Cache-Control: no-cache`
  so the browser re-fetches it on each navigation and notices changes. Bump
  `CACHE_VERSION` on any shell/asset change.

## Q6 / Q7 / Q8 — Templates, apple meta, and the 037/038 collision

- **No shared base** — `landing.html` and `dashboard.html` are each standalone
  documents (`landing.html:3-12`, `dashboard.html:3-12`); both `import _macros.html`
  but neither `{% extends %}`. → the manifest link + apple meta + SW-registration
  script must be added to **both** heads.
- **Current head** (both): `charset`, `viewport` (`:5` —
  `width=device-width, initial-scale=1`, 037 adds `viewport-fit=cover`), `title`,
  Google-Fonts preconnect + stylesheet, `/static/landing.css`; dashboard adds
  `/static/dashboard/dashboard.css`. **No** manifest/apple/theme-color/icon tags
  exist yet.
- **iPadOS standalone-install tags (Q7):**
  `<link rel="manifest" href="/manifest.webmanifest">`,
  `<link rel="apple-touch-icon" href="/static/icons/apple-touch-icon-180.png">`,
  `<meta name="apple-mobile-web-app-capable" content="yes">` **and**
  `<meta name="mobile-web-app-capable" content="yes">`,
  `<meta name="apple-mobile-web-app-status-bar-style" content="black-translucent">`
  (pairs with 037's `viewport-fit=cover`),
  `<meta name="apple-mobile-web-app-title" content="Hive">`,
  `<meta name="theme-color" content="#e0a726">`, plus a favicon link.
- **Collision (Q8).** All of 037/038/039/040 edit `src/hive/web`; 037 + 040 both
  touch the **`<head>`** (037 the viewport meta `:5`; 040 adds tags after it).
  Not a *logical* blocker, but a textual one → **040 rebases after 037 merges**
  (037 is do-first). 038/039 touch the rail/body, minimal head overlap.

## Q9 / Q10 — Icons + packaging

- **No existing icon/logo** anywhere (`src/hive/web/static` holds only
  `landing.css` + `dashboard/*.{jsx,css,js}`; the only PNG in the repo is
  `tests/fixtures/sample.png`). → generate placeholders from scratch.
- **Brand palette** (`landing.css:7-23`): paper `#faf7ed`, ink `#1f1812`, honey
  `#e0a726`, amber `#b7741a`. Placeholder = a honeycomb-hexagon "Hive" mark, honey
  on paper. **Generated dev-time** (author one SVG → rasterize to PNG with an
  available tool); the committed outputs are plain PNG/ICO → **no runtime image
  dependency**. Set: `icon-192.png`, `icon-512.png`, `icon-512-maskable.png`
  (safe-zone padded, `"purpose":"maskable"`), `apple-touch-icon-180.png`
  (opaque, no alpha), `favicon.ico`.
- **Packaging (Q10).** `pyproject.toml:37-38` has only `[tool.setuptools.packages.
  find] where=["src"]` — **no** `package-data`/`include_package_data`/`MANIFEST.in`.
  But production runs from the **source tree** (the service imports
  `~/projects/hive/src`), and the *existing* static assets (`landing.css`, the
  JSX) already serve in prod — so new files under `static/` ship too under the
  current model. → `package-data` is **optional hardening** (only matters for a
  wheel/non-editable install), **not** required for this deploy. Out of scope to
  keep the change minimal.

## Q11 — Test surface: what units can prove vs. what needs the iPad

- **Pattern** (10 web test modules, ~1,658 LOC, all the same shape):
  `from fastapi.testclient import TestClient; TestClient(create_app(process_manager
  =_bare_pm(), **stores))` (e.g. `tests/test_web_landing.py:25-26`). Static-asset
  assertion to copy: `tests/test_web_landing.py:38-42` (200 + content-type);
  markup assertion: `tests/test_web_landing.py:30-36` (`assert '…' in resp.text`).
- **Units CAN prove** (no secure context needed — `TestClient` is in-process):
  `GET /manifest.webmanifest` → 200 + `application/manifest+json` + valid fields;
  `GET /service-worker.js` → 200 + JS media type + `no-cache` + contains the
  lifecycle handlers; `GET /` and `/dashboard` HTML contain the manifest link +
  apple meta + theme-color + SW-registration script; `GET /static/icons/icon-192.
  png` → 200 + `image/png`; each manifest `icons[].src` resolves to 200.
- **Only a real iPad over HTTPS CAN prove** (the DoD): the SW actually
  **registers**; **Add to Home Screen** launches **standalone** (no Safari chrome)
  with the icon + status-bar style; the **offline shell** renders with Wi-Fi off;
  `/api/*` still works online (SW didn't break live data). A `curl`-200 is
  insufficient — Safari must register the SW and honour the manifest.
- **CI gates** (`CLAUDE.md:157-164`, `.github/workflows/ci.yml:23-29`):
  `ruff check src/ tests/` **and** `ruff format --check src/ tests/` (separate
  gates), `pytest -m "not integration" --cov` with a **75%** floor
  (`pyproject.toml:61`).

## Decision-record scope (Q12)

"Serve the dashboard over HTTPS via `tailscale serve` to unlock the
secure-context PWA features" is **ADR-worthy** — it changes how **every** device
reaches Hive (HTTP IP → HTTPS hostname), it's the hard gate for the whole PWA
line, and **041 (push) builds directly on it**. → **ADR 0023** (provisional;
re-check at ship — numbers race across worktrees). The reference-doc side effect
(`docs/DEPLOYMENT.md`: the `tailscale serve` step + new access/smoke URL) is
declared in `plan.md` (cross-cutting). **No `CONTEXT.md` term** — PWA / service
worker / secure context are generic web vocabulary, not Hive domain terms.
