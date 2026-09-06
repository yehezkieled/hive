# Questions — Ticket 042: iPad web polish & token-entry UX

The unknowns going in. Each is answered with code evidence in `research.md`;
the one-line verdicts are repeated here for a fast read.

## Q1 — Token entry: why does it re-prompt, and what's the safe fix?

- Where is the web token stored client-side today, and why does that cause a
  re-prompt on the iPad?
- Does persisting it change the **server-side** security model at all?
- Which of A (`localStorage`) / B (drop token, trust the tailnet) / C (login
  screen) is the right call for a single-user, tailnet-bound tool?

**Verdict:** token lives in `sessionStorage` (tab-scoped) → iOS Safari drops
backgrounded tabs → re-prompt. The token already gates **writes only**; reads
already rely on the Tailscale bind alone. → **Option A (`localStorage`)**:
client-only change, server untouched, security model unchanged. (Decided with
the developer in S9 planning.)

## Q2 — Are the `+ New` / `History` header buttons truly dead?

- Do they have *any* handler (inline, listener, delegated), or are they pure
  placeholders?
- Does anything in the layout depend on them?

**Verdict:** zero handlers anywhere; styled by `.btn` only. Removing the
`.chat-rail__head-actions` span is layout-safe (`margin-left:auto` keeps the
drawer-close `x` right-aligned). → **remove**.

## Q3 — Is the keyboard-up composer gap a real bug or stale cache?

- The 037 re-smoke photo showed an odd gap with the keyboard up. Is it a real
  `--kb`/safe-area layout bug, or just the stale shell from Q4?
- If real, what is the minimal CSS fix — and *which* element is actually
  involved?

**Verdict:** **verify on-device after the cache fix.** The composer already
lifts correctly (it rides inside the `.chat-rail` fixed overlay, whose `bottom`
includes `--kb`). The `.terminal-bar` is **not** involved — it's a
`position:relative` footer *behind* the drawer, not adjacent to the composer.
If a gap survives a cache clear, the likely cause is a **safe-area double-count**
(chat-rail `bottom` + composer `padding-bottom` both add
`env(safe-area-inset-bottom)`), a ~20–35px gap — fixed by dropping the
redundant inset from the composer in narrow mode.

## Q4 — What's the minimal cache fix that kills the stale shell without breaking offline?

- How is `landing.css` served by the service worker today, and why does it go
  stale after a deploy?
- Can we serve it network-first without breaking the offline shell
  (`offline.html`)?

**Verdict:** all `/static/*` (incl. `landing.css`) is **stale-while-revalidate**,
so a deploy shows old CSS on the next load until `CACHE_VERSION` is manually
bumped. → serve **`landing.css` network-first**; keep `offline.html`
cache-first + precached so offline navigation still works; bump `CACHE_VERSION`.
Auto-bumping from a build hash is **out of scope** (would make `service-worker.js`
a dynamic route).
