# Design — Ticket 042: iPad web polish & token-entry UX

Four independent polish fixes on the web shell, shipped as **one PR** (direct
lane). All touch `src/hive/web`; none changes the command/decision protocol or
the responsive shell (037 owns that). Decisions are `D1–D4`, one per item.

---

## D1 — Token: persist in `localStorage` (Option A)

**Decision.** Move the web token from `sessionStorage` to `localStorage` so the
iPad prompts **once per device**, not once per tab.

**Why A over B/C** (see `research.md` R1 for the code):
- The token is **defense-in-depth on writes only** — reads already rely on the
  `127.0.0.1`/Tailscale bind. Persisting it changes the *durability* of that
  layer, not the *model*. Server code is untouched.
- **B (drop token, trust tailnet)** throws the write-protection away or replaces
  it with Tailscale-IP detection that's only safe behind a reverse proxy — too
  much surface and risk for a polish ticket, and a binary failure mode.
- **C (login screen)** adds a `/login` route + cookie handling for a single-user
  tool — overkill.

**Shape.** Swap the storage calls (`landing.html` ×6 sites, `refresh.js` ×1) from
`sessionStorage` → `localStorage`. Keep the `401`-clears-token path (it now does
`localStorage.removeItem`) as the de-facto sign-out. Reword the modal hint
**"Stored for this tab only" → "Stored on this device"** so the UX copy matches.

**Accepted trade-off.** The token persists until browser-data clear; there's no
explicit "sign out" button. Fine for a personal iPad; a sign-out affordance is a
tiny **out-of-scope** follow-up if ever wanted.

---

## D2 — Remove the dead `+ New` / `History` buttons

**Decision.** Delete the `.chat-rail__head-actions` span
(`landing.html:69–72`) outright — they have no handlers (R2) and read as
tappable-but-broken on touch.

**Why remove, not wire.** "New chat" / "History" are real features that don't
exist yet; faking affordances for them is worse than their absence. Wiring them
is its own ticket, not 042 polish.

**Hygiene.** Also drop the now-orphan `.chat-rail__head-actions` CSS rule
(`landing.css:289–293`). Layout is safe — `margin-left:auto` keeps the
drawer-close `x` right-aligned with the span gone.

---

## D3 — Cache: serve `landing.css` network-first; bump `CACHE_VERSION`

**Decision.** Split the service-worker `/static/*` handler so **`landing.css` is
network-first** (fresh every load, cached copy as the offline fallback); all other
`/static/*` stays stale-while-revalidate; `offline.html` stays cache-first +
precached. Bump `CACHE_VERSION` `hive-v2 → hive-v3` so existing installs flush
on activate.

**Why.** SWR served the *old* `landing.css` on the first load after every deploy
(R4) — the root cause of the stale-shell symptom. Network-first removes the
staleness for the one asset that defines the shell, while the offline guarantee
(navigations → `offline.html`) is untouched.

**Why NOT auto-bump from a build hash.** That requires templating
`service-worker.js` (a static file today) into a dynamic route — real machinery,
out of proportion to a polish ticket. Network-first makes the manual bump
**no longer load-bearing for CSS**, which is the actual pain. Auto-bump is noted
as a deferred nice-to-have, not built here.

**Guardrail.** Do **not** move `offline.html` out of precache or off cache-first —
it's the only offline-navigation fallback.

> This refines **040 D4** for a single asset. Small and code-local → documented
> here, **no new ADR** (ADR 0025 is the latest; 042 introduces no decision of
> record).

---

## D4 — Keyboard gap: verify on-device, then fix the safe-area double-count

**Decision.** This item is **verification-gated**, in this order:
1. Land D3 (cache fix) + the `CACHE_VERSION` bump; hard-refresh the installed PWA.
2. Re-smoke the keyboard-up composer on a real iPad (portrait + landscape).
3. **If** the gap is gone → it was stale cache; close the item, no CSS change.
4. **If** the gap survives → apply the safe-area fix: in narrow mode
   (`@media (max-width:900px)`), drop the redundant `env(safe-area-inset-bottom)`
   from `.composer`'s `padding-bottom` (flat `14px`), since `.chat-rail`'s
   `bottom` already owns the safe-area + `--kb` offset (R3).

**Why gated.** The composer already lifts correctly; the `.terminal-bar` theory is
wrong (R3). The only plausible real cause is the double-counted inset, which is
device-dependent (iOS keeps `safe-area-inset-bottom` non-zero with the keyboard
up). Changing CSS *before* ruling out the stale shell risks "fixing" a
cache artifact and introducing a real regression. Carries the remaining **037
keyboard-visibility re-smoke** (sent message + reply stay above the keyboard).

---

## Cross-cutting / side effects

- **CONTEXT.md (glossary):** no change — 042 introduces no new terms.
- **ADR:** none — D3 refines 040 D4 in-ticket; no decision of record.
- **DEPLOYMENT.md / README:** no change — `CACHE_VERSION` is code, not a runbook
  step. (042 is **not** a cross-cutting Ticket; no `✱`.)

## Alternatives considered (rejected)

- **Token: B / C** — see D1.
- **Cache: make *all* `/static/*` network-first** — slower loads + weaker offline
  for assets that don't define the shell; landing.css alone is the one that bit
  us.
- **Keyboard: fix the CSS now without re-smoke** — risks regressing on a cache
  artifact; rejected for the gated approach.
