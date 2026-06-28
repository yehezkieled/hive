# Outline — Ticket 042: iPad web polish & token-entry UX

Implementation structure for the single build PR. Order is chosen so the cache
fix (D3) lands **before** the keyboard re-smoke (D4) it gates. Exact line numbers
live in `research.md`; confirm against the file at build time.

## Step 1 — D1: token → `localStorage`

- `src/hive/web/templates/landing.html` — replace `sessionStorage` with
  `localStorage` at all token sites: `ensureToken()` get/set (`~287`, `~305`),
  the `401` clear (`~704`), the SSE token read (`~1090`), and the vault/mode/
  decision reads (`~463`, `~527`, `~598`). Reword modal hint (`~236`):
  "Stored for this tab only" → "Stored on this device".
- `src/hive/web/static/dashboard/refresh.js` — `getToken()` (`~14`):
  `sessionStorage` → `localStorage`.
- Grep guard: `grep -rn sessionStorage src/hive/web` returns **nothing** after.

## Step 2 — D2: remove dead header buttons

- `src/hive/web/templates/landing.html` — delete the
  `.chat-rail__head-actions` span (`69–72`).
- `src/hive/web/static/landing.css` — delete the orphaned
  `.chat-rail__head-actions` rule (`289–293`).

## Step 3 — D3: `landing.css` network-first + cache bump

- `src/hive/web/static/service-worker.js`:
  - `CACHE_VERSION` `'hive-v2'` → `'hive-v3'` (`:11`).
  - In the fetch handler, **before** the `/static/*` SWR branch (`69–85`), add a
    `url.pathname === '/static/landing.css'` branch: `fetch()` first, clone →
    `cache.put`, `.catch(() => caches.match(request))` as offline fallback.
  - Leave navigations (`55–67`) and `offline.html` precache untouched.

## Step 4 — D4: keyboard re-smoke (gated), fix only if it survives

- Deploy Steps 1–3; hard-refresh / reinstall the PWA on the iPad.
- Re-smoke keyboard-up composer (portrait + landscape) + the carried 037
  visibility check (sent message + reply stay above the keyboard).
- **Only if the gap persists:** `src/hive/web/static/landing.css`, narrow block
  (`@media (max-width:900px)`) — set `.composer { padding-bottom: 14px }` (drop
  the `max(14px, env(safe-area-inset-bottom))` there; the `.chat-rail` `bottom`
  already owns the inset + `--kb`). Re-smoke again.

## Validation gate (the one PR)

- `ruff check src/ tests/ && ruff format --check src/ tests/` (touches `.py`?
  only if `refresh.js`/templates — but run anyway; CI gates both).
- `pytest -m "not integration"` green (no Python logic changed; confirms nothing
  broke).
- **Deployed on-device re-smoke** (the real gate — Safari downloads/compiles/
  mounts; curl-200 is insufficient): token prompts once then persists across a
  tab close; header shows no dead buttons; CSS is current after deploy without a
  manual bump dance; keyboard-up composer sits flush above the keyboard.

## Sequencing note

Steps 1–3 are independent edits in the same files — do them in one branch, one
commit set. Step 4's CSS change is **conditional**; if the re-smoke is clean,
the PR ships with Steps 1–3 only and D4 closes as "was stale cache".
