# Research — Ticket 042: iPad web polish & token-entry UX

Code evidence behind every `design.md` decision. Grounded in a 4-concern read of
`src/hive/web` (token auth, dead buttons, SW cache, keyboard layout). Line
numbers are as of branch-off from `origin/main` and may drift — treat them as
"look here," not gospel.

---

## R1 — Token auth: stored tab-scoped, gates writes only

**How it works today.** The web token (`hive_web_token`) is read/written in
`sessionStorage`, which the browser clears when the tab closes — and iOS Safari
evicts backgrounded tabs aggressively, so the iPad re-prompts constantly.

| Where | File:line | What |
|-------|-----------|------|
| Read + modal | `templates/landing.html:286–317` | `ensureToken()` reads `sessionStorage.getItem('hive_web_token')`; on miss shows the in-page `.token-modal` and `sessionStorage.setItem()`s the result. |
| Modal hint | `templates/landing.html:228–246` | `.token-modal`, password input, hint **"Stored for this tab only"** (`:236`). In-page modal (not `window.prompt`) for iOS Safari. |
| Send path | `templates/landing.html:653–704` | `sendChatCommand()` `await ensureToken()` then `Authorization: Bearer <token>`; on `401` does `sessionStorage.removeItem()` (the only "logout"). |
| SSE | `templates/landing.html:1088–1098` | `EventSource('/sse/notifications?token=…')` — token as **query param** (EventSource can't set headers). |
| Other writes | `templates/landing.html:463, 527, 598` | vault / mode / decision buttons each read `sessionStorage` + send Bearer. |
| Dashboard | `static/dashboard/refresh.js:12–18` | `getToken()` reads `sessionStorage`; used for `/api/dashboard/all`. |
| Server check | `web/auth.py:21–44` | `require_token()`: empty `WEB_TOKEN` → 401 "disabled"; else accept `Authorization: Bearer` **or** `?token=` via `hmac.compare_digest`. |
| Gated routes | `web/app.py` | **writes** gated: `/api/command`, `/api/decision/*`, `/api/vault-action/*`, `/api/mode-request/*`, `/api/gate/*`, `/sse/notifications`, `/api/dashboard/all`. **Reads open**: `/`, `/api/landing/*`, `/api/status`, `/api/org`, … |
| Bind | `config.py:100–108`, `__main__.py:406` | `WEB_HOST` defaults `127.0.0.1` ("Do not flip to 0.0.0.0 until auth ships"); uvicorn binds `WEB_HOST`. |

**Load-bearing fact for the decision:** the token is **defense-in-depth on
writes**, layered on top of the `127.0.0.1`/Tailscale bind that already gates
all reads. Persisting it (Option A) changes *nothing* server-side.

**Option cost, from the code:**
- **A — `localStorage`:** swap the storage calls (`landing.html` ×6, `refresh.js`
  ×1) + reword the modal hint. **Server: untouched.** Trade-off: token persists
  until browser-data clear; no explicit sign-out today (the `401` path still
  clears it).
- **B — drop the token, trust the tailnet:** rewrite `auth.py` to a Tailscale-IP
  check; remove the modal. **Risky** — safe only while bound to loopback; needs a
  reverse proxy + `X-Forwarded-For` to stay safe if `WEB_HOST` ever moves to
  `0.0.0.0`. Binary failure mode (all-allow / all-deny).
- **C — login screen:** new `GET/POST /login` route + cookie/localStorage token;
  `auth.py` learns a cookie path. Moderate; overkill for one user.

**Chosen: A** (see `design.md` D1).

---

## R2 — `+ New` / `History` header buttons are pure placeholders

| File:line | What |
|-----------|------|
| `templates/landing.html:69–72` | `<span class="chat-rail__head-actions"><button class="btn">History</button><button class="btn btn--primary">+ New</button></span>` — no `onclick`, no `id`, no `data-*`. |
| `templates/landing.html` (full grep) | Zero `addEventListener` / delegated handler targets these. The `[data-cmd]` delegate (`:846`) hits maestro cards + composer chips, not these. |
| `static/landing.css:289–293` | `.chat-rail__head-actions` = `margin-left:auto; display:flex; gap:6px`. |
| `static/landing.css:1618–1626` | under `@media (pointer:coarse)` `.btn` grows to `min-height:44px` (037 floor) — no other `.btn` in this header, so nothing is lost. |

**Removal is layout-safe:** deleting the span leaves `.chat-rail__head` intact;
the drawer-close `x` (`:73`) stays right-aligned. The CSS rule at `289–293`
becomes dead and can be removed for hygiene (no functional impact if left).

---

## R3 — Keyboard-up composer gap: terminal-bar is NOT the culprit

The sweep's first pass blamed a `.terminal-bar` ↔ `.chat-rail` gap. **Reading the
layout disproves that** — recorded here so the implementer doesn't chase it.

| File:line | What |
|-----------|------|
| `static/landing.css:1577–1589` | Narrow mode (`max-width:900px`): `.chat-rail` becomes `position:fixed; inset:0; bottom: calc(env(safe-area-inset-bottom) + var(--kb,0px))`. The composer rides **inside** this overlay, so it lifts with `--kb`. ✓ |
| `templates/landing.html:1021–1036` | `visualViewport` listener sets `--kb = max(0, innerHeight − vv.height − vv.offsetTop)` — the keyboard gap. |
| `static/landing.css:604–612` | `.composer` `padding-bottom: max(14px, env(safe-area-inset-bottom))`. |
| `static/landing.css:1354–1372` | `.terminal-bar` is `position:relative` (`:1369`), `flex-shrink:0`, a **footer in main document flow**. |
| `static/landing.css:1600–1605` | When the drawer is open the terminal-bar drops to `z-index:38` — it sits **behind** the drawer, not beside the composer. |

**Conclusion.** The composer already lifts above the keyboard correctly. The
terminal-bar is in a different stacking context behind the drawer — the imagined
330px gap is an artifact of mis-reading the layout. **The only plausible *real*
gap** is a **safe-area double-count**: `.chat-rail`'s `bottom` adds
`env(safe-area-inset-bottom)` *and* `.composer`'s `padding-bottom` adds it again
(iOS keeps that inset non-zero with the keyboard up) → a ~20–35px gap above the
keyboard. Device-dependent → **verify on-device after R4 lands** before touching
CSS. If confirmed, drop the redundant inset from the composer in narrow mode
(the chat-rail already owns the safe-area + `--kb`).

---

## R4 — Stale shell: `landing.css` is stale-while-revalidate

| File:line | What |
|-----------|------|
| `static/service-worker.js:11` | `CACHE_VERSION = 'hive-v2'` — single cache namespace. |
| `static/service-worker.js:17–22` | `PRECACHE_URLS` includes `/static/landing.css` **and** `/offline.html` + icons. |
| `static/service-worker.js:69–85` | `/static/*` → **stale-while-revalidate**: serves cached copy first, updates cache in the background. `landing.css` rides this. |
| `static/service-worker.js:55–67` | navigations → **network-first** → `offline.html` fallback. |
| `static/service-worker.js:33–42` | `activate()` deletes caches `!== CACHE_VERSION` → invalidation is **manual-bump-only**. |
| `docs/tickets/040-pwa-install/design.md:23–24` | 040 D4: navigations net-first → offline; `/static/*` SWR; `/api/*` net-only. D5: bump `CACHE_VERSION` on shell change. |

**Why it bit us:** SWR serves the *old* `landing.css` on the load right after a
deploy and only freshens for the *next* load — so a deploy that forgot the bump
showed a stale shell (the landscape drawer-toggle that live CSS hides). **Fix:**
split the `/static/*` handler so `landing.css` is **network-first** (fresh every
load, cached copy as offline fallback); everything else stays SWR; `offline.html`
stays cache-first + precached (do **not** move it). This refines 040 D4 for one
asset — small enough to live in this ticket, **no new ADR**.

> Trade-off: network-first means `landing.css` waits on the network each load —
> negligible on the tailnet for a small file, and correctness wins over a few ms.
