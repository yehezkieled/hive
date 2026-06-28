# Plan — Ticket 042: iPad web polish & token-entry UX  (issue #225)

**Lane:** direct — one branch, one PR that closes #225.
**Sprint:** 2026-Q2-S9. Decisions: `design.md` D1–D4. Build map: `outline.md`.

## Files this Ticket creates / modifies

| Path | Op | Step (design ref) |
|------|----|-------------------|
| `src/hive/web/templates/landing.html` | modify | D1: `sessionStorage`→`localStorage` ×6 + modal hint copy. D2: delete `.chat-rail__head-actions` span. |
| `src/hive/web/static/dashboard/refresh.js` | modify | D1: `getToken()` → `localStorage`. |
| `src/hive/web/static/landing.css` | modify | D2: delete orphan `.chat-rail__head-actions` rule. D4 *(conditional)*: drop redundant safe-area from `.composer` padding in narrow mode — **only if** the keyboard gap survives the cache fix. |
| `src/hive/web/static/service-worker.js` | modify | D3: `landing.css` network-first branch + `CACHE_VERSION` `hive-v2`→`hive-v3`. |

No new files. No Python logic change. Not cross-cutting (no reference-doc edits).

## Verification

- `grep -rn sessionStorage src/hive/web` → **no matches** (D1 complete).
- `ruff check src/ tests/ && ruff format --check src/ tests/` green.
- `pytest -m "not integration"` green (confirms nothing broke; no logic changed).
- **Deployed on-device re-smoke on a real iPad** (the real gate — Safari
  downloads/compiles/mounts; curl-200 is not enough), portrait + landscape:
  1. Token prompts **once**, then persists across a tab close / app background.
  2. Header shows **no** `+ New` / `History` buttons.
  3. After a deploy, `landing.css` is **current** without a manual cache dance.
  4. Keyboard-up composer sits **flush** above the keyboard; sent message + reply
     stay visible (carries the remaining 037 keyboard re-smoke).

## Out of scope

- New shell/layout redesign (037) · Web Push (041) · status-bar overlap (043).
- Explicit "sign out" button (D1 trade-off) · auto-bumping `CACHE_VERSION` from a
  build hash (D3) — both noted as deferred nice-to-haves.

## Cross-cutting impact

- None. CONTEXT.md unchanged; no new ADR (D3 refines 040 D4 in-ticket);
  no DEPLOYMENT.md / README change.

## Build handoff

Direct lane — build as **one PR that closes #225**. The conditional D4 CSS edit
ships in the same PR only if the on-device re-smoke confirms the gap is real;
otherwise the PR is Steps 1–3 and D4 closes as "was stale cache".
