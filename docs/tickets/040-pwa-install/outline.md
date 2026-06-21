# Outline — Ticket 040: PWA install (home-screen app)

Ordered implementation structure for **one builder, one PR** (direct lane). Each
step names the files and the decision (`Dn`) it implements. Code (steps 1–5) and
the docs edit (6) ship in the PR; the secure-context enablement (7) is the
one-time ops step the PR's verification depends on.

> **Scope fence:** new static assets + a manifest route + a root-scoped SW route +
> `<head>` tags in **both** templates + a `DEPLOYMENT.md` edit. **No** app-logic
> change; **no** `/api` change; **no** `package-data` (D8); **no** push (041).
> All new URLs are **relative** (host-agnostic across the origin move).

## Steps

1. **Generate the icon set** — *D7*
   Author one `src/hive/web/static/icons/hive-mark.svg` (honeycomb hexagon, honey
   `#e0a726` on paper `#faf7ed`). Rasterize **dev-time** to
   `static/icons/{icon-192.png, icon-512.png, icon-512-maskable.png,
   apple-touch-icon-180.png}` + `static/icons/favicon.ico`. Commit the outputs +
   the SVG. *First* — the manifest and head tags reference these paths. No runtime
   image dep (committed PNG/ICO).

2. **Manifest + route** — *D2*
   Create `static/manifest.webmanifest` (or build inline) with `name`/`short_name`
   "Hive", **relative** `start_url:"/"` + `scope:"/"`, `display:"standalone"`,
   `theme_color:"#e0a726"`, `background_color:"#faf7ed"`, the 3-icon array.
   `src/hive/web/app.py`: new `@app.get("/manifest.webmanifest")` returning it with
   `media_type="application/manifest+json"` (copy the root-route pattern at
   `:476`).

3. **Service worker + offline shell + route** — *D3, D4, D5*
   Create `static/service-worker.js`: `CACHE_VERSION` const; `install` precaches
   the shell + `/offline.html` + icons + `skipWaiting()`; `activate` deletes
   non-current caches + `clients.claim()`; `fetch` routes by class (network-only
   `/api/*`+`/sse/*`+POST; network-first navigations → `/offline.html`; SWR
   `/static/*`; cache-first opaque CDN). Create `static/offline.html` (minimal
   "Hive — reconnecting…" shell, brand palette, inline CSS). `app.py`: new
   `@app.get("/service-worker.js")` → `FileResponse(..., media_type=
   "application/javascript")` **with `Cache-Control: no-cache`**.

4. **Head tags + SW registration** — *D6*
   In **both** `templates/landing.html` and `templates/dashboard.html` heads (after
   the viewport meta `:5`): `<link rel="manifest">`, `<link rel="apple-touch-icon">`,
   `apple-mobile-web-app-capable` + `mobile-web-app-capable`,
   `apple-mobile-web-app-status-bar-style=black-translucent`,
   `apple-mobile-web-app-title=Hive`, `<meta name="theme-color" content="#e0a726">`,
   favicon link, and a guarded inline `<script>` registering `/service-worker.js`.
   (Rebase **after** 037, which also edits `:5`.)

5. **Tests** — *DoD (unit half)*
   New `tests/test_web_pwa.py` (copy `tests/test_web_landing.py:25-26,30-42`):
   `GET /manifest.webmanifest` → 200 + `application/manifest+json` + fields;
   `GET /service-worker.js` → 200 + JS type + `no-cache` + lifecycle handlers
   present; `GET /` and `/dashboard` HTML contain the manifest link + apple meta +
   theme-color + SW registration; `GET /static/icons/icon-192.png` → 200 +
   `image/png`; each manifest `icons[].src` resolves to 200.

6. **`DEPLOYMENT.md` (cross-cutting)** — *D1*
   Document: enable MagicDNS + HTTPS certs in the tailnet admin; the exact
   `tailscale serve` invocation; `HIVE_WEB_HOST=127.0.0.1` (loopback re-bind); the
   new access URL `https://<node>.tailfb3900.ts.net/`; replace the
   `http://100.79.194.84:8080` smoke URL. Reference **ADR 0023**.

7. **Enable HTTPS + lint/units + real-iPad re-smoke** — *DoD gate*
   On the VPS: admin HTTPS toggle → `tailscale serve` → set loopback host →
   `systemctl --user restart hive.service` → `journalctl` clean.
   `ruff check src/ tests/ && ruff format --check src/ tests/` (separate gates);
   full `pytest -m "not integration"`; confirm **real CI** green.
   **Real iPad over the HTTPS hostname:** SW registers (no console error); **Add to
   Home Screen** → launches **standalone** (no Safari chrome) with the icon +
   status-bar style; **Wi-Fi off** → the offline shell renders; **online** →
   `/api/*` + SSE still work (SW didn't break live data). A curl-200 is
   insufficient — Safari must register the SW and honour the manifest.

## Build sequencing note

1→2→3 are the asset/route core (icons before the manifest that names them; the SW
+ offline shell before its route). 4 (head tags) needs 2 + 3 to point at. 5
(tests) follows the code. 6 (docs) is independent and can be written any time. 7
is last and is the only step that needs the live VPS + device. The whole thing is
**one cohesive PR** — slicing manifest/SW/meta/icons across PRs would only churn
the same files and the same `<head>`.
