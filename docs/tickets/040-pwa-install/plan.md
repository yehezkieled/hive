# Plan — Ticket 040: PWA install (home-screen app)  (issue #205)

**Lane:** direct — one cohesive PR. The PWA artifacts (manifest + service worker +
apple meta + icons) all ship together to make "it installs"; slicing them would
churn the same files and the same `<head>`. The secure-context decision and
**[ADR 0023](../../adr/0023-https-via-tailscale-serve-for-pwa.md)** already shipped
with `design.md`; the build PR adds the code + a `DEPLOYMENT.md` edit and **closes
#205**. Decisions: `design.md` (D1–D8); structure: `outline.md`; evidence:
`research.md`.

> **The pivot:** a service worker needs a **secure context**; Hive is plain HTTP
> today, so 040 first serves the dashboard over **HTTPS via `tailscale serve`**
> (loopback re-bind, single HTTPS origin — ADR 0023), then adds the PWA artifacts.
> All new URLs are **relative** so nothing breaks across the origin move.

## Files this Ticket creates / modifies

| Path | Op | Step (decision) |
|------|----|-----------------|
| `src/hive/web/static/icons/hive-mark.svg` | **create** | Source mark (honeycomb hexagon, honey `#e0a726` on paper `#faf7ed`) — D7. |
| `src/hive/web/static/icons/icon-192.png` · `icon-512.png` · `icon-512-maskable.png` · `apple-touch-icon-180.png` · `favicon.ico` | **create** | Rasterized **dev-time** from the SVG; committed PNG/ICO → no runtime image dep — D7. |
| `src/hive/web/static/manifest.webmanifest` | **create** | `name`/`short_name` "Hive", relative `start_url:"/"`+`scope:"/"`, `display:"standalone"`, `theme_color:"#e0a726"`, `background_color:"#faf7ed"`, 3-icon array — D2. |
| `src/hive/web/static/service-worker.js` | **create** | `CACHE_VERSION`; `install` precache shell+`/offline.html`+icons+`skipWaiting()`; `activate` delete-old+`clients.claim()`; `fetch` per-class routing (network-only `/api`+`/sse`+POST; network-first nav→offline; SWR `/static`; cache-first opaque CDN) — D4, D5. |
| `src/hive/web/static/offline.html` | **create** | Minimal "Hive — reconnecting…" shell, inline CSS, brand palette — D4. |
| `src/hive/web/app.py` | modify | New `@app.get("/manifest.webmanifest")` (`application/manifest+json`) + `@app.get("/service-worker.js")` (`FileResponse`, `application/javascript`, **`Cache-Control: no-cache`**), copying the root-route pattern at `:476` — D2, D3. |
| `src/hive/web/templates/landing.html` | modify | After viewport meta `:5`: manifest link, apple-touch-icon, `apple-mobile-web-app-capable`+`mobile-web-app-capable`, `…-status-bar-style=black-translucent`, `…-title=Hive`, `theme-color`, favicon, guarded SW-registration `<script>` — D6. **Rebase after 037** (also edits `:5`). |
| `src/hive/web/templates/dashboard.html` | modify | Same head block as landing (`:3-12`) — no shared base — D6. |
| `tests/test_web_pwa.py` | **create** | Routes (200 + content-types + `no-cache` + lifecycle handlers), head markup in `/`+`/dashboard`, icon 200s; `TestClient` pattern from `tests/test_web_landing.py:25-26,30-42` — DoD (unit half). |
| `docs/DEPLOYMENT.md` | modify | **Cross-cutting** — the `tailscale serve` step, loopback re-bind, new HTTPS access + smoke URL; reference ADR 0023 — D1. |
| `docs/adr/0023-https-via-tailscale-serve-for-pwa.md` | (already shipped) | The HTTPS-via-Tailscale decision. **Re-check the number at ship — ADRs race across worktrees.** |

No `pyproject.toml` `package-data` change (D8 — prod runs from the source tree).

## Verification

Acceptance is only fully provable on the device over HTTPS — units/lint gate, the
iPad re-smoke confirms.

- `ruff check src/ tests/` **and** `ruff format --check src/ tests/` both green
  (separate CI gates — fixing lint does not fix format).
- Full `pytest -m "not integration"` green (incl. `tests/test_web_pwa.py`); confirm
  **real CI** green, not a scoped local run. Coverage stays ≥75%.
- **Enable HTTPS (one-time ops):** tailnet admin → MagicDNS + HTTPS certificates;
  `tailscale serve` the dashboard; `HIVE_WEB_HOST=127.0.0.1`; `systemctl --user
  restart hive.service`; `journalctl --user -u hive.service -n 20` clean.
- **Real iPad, over `https://<node>.tailfb3900.ts.net/`:**
  - Service worker **registers** (no console error; app boots).
  - **Add to Home Screen** → launches **standalone** (no Safari address bar) with
    the Hive icon + `black-translucent` status bar. (Core DoD.)
  - **Wi-Fi off** → the offline shell renders (not Safari's error page).
  - **Online** → `/api/*` commands/approvals + `/sse/notifications` still work
    (the SW did **not** cache/break live data).
- **Another tailnet device** → confirm it reaches the new HTTPS URL (the loopback
  re-bind removed the old `http://100.79.194.84:8080` path).

## Open questions for the builder

- **Exact `tailscale serve` invocation** — syntax differs by Tailscale version
  (`tailscale serve https / http://127.0.0.1:8080` vs `--bg` forms). Confirm on the
  VPS; allow ~a minute for first cert issuance.
- **CDN opaque-cache cost** — Babel-standalone (~3 MB). Runtime-cache it (don't
  precache at `install`) so the landing-shell install stays light; confirm the
  landing shell boots offline, accept that React `/dashboard` offline is
  best-effort.
- **Scroll/`/offline.html` styling** — keep it inline-CSS + brand palette so it
  needs no cached `/static` to render.

## Out of scope

- **Web Push / VAPID (041)** — separate ticket, blocked on this.
- Offline command queue / IndexedDB sync; full offline functionality (shell +
  cached static only).
- React `/dashboard` offline-boot as a guaranteed line (best-effort).
- `[tool.setuptools.package-data]` (D8 — only needed for a wheel install).
- 037's touch-shell / 038's decision API / 039's attention badge.

## Cross-cutting impact

- **ADR:** [0023](../../adr/0023-https-via-tailscale-serve-for-pwa.md) — shipped
  with `design.md` (provisional number; re-check at ship). The HTTPS-origin
  decision 041 builds on.
- **`docs/DEPLOYMENT.md`:** edited in the **build PR** — the `tailscale serve`
  step, loopback re-bind, and new HTTPS access/smoke URL. Declared here upfront.
- **`CONTEXT.md`:** no new glossary term (PWA / service worker / secure context are
  generic web vocabulary).
- **README / ARCHITECTURE:** no change.
- **`INDEX.md`:** flip 040 → `in progress` (issue #205) now; → `done` at merge.
- **`CHANGELOG.md` + the `CLAUDE.md` sprint pointer:** at **S8 close**, not this PR.
- **Rebase note:** 037–040 all edit `src/hive/web`; 040 rebases after 037 (shared
  `<head>`). Gates 041.

## To build

Single PR, branch `ticket-040/pwa-install` (or via the run skill) following
`outline.md` steps 1–7; closes #205.
