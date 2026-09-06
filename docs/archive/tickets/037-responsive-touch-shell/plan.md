# Plan — Ticket 037: Responsive / touch shell  (issue #200)

**Lane:** direct — one cohesive PR. All changes land in the same two
tightly-coupled files (`landing.html` + `landing.css`); slicing five tiny
presentation edits across the same files would only create rebase conflicts.
The drawer-based design and **[ADR 0022](../../adr/0022-responsive-touch-shell-contract.md)**
already shipped with `design.md`; the build PR touches only the two web files and
**closes #200**. Decisions: `design.md` (D1–D9); structure: `outline.md`;
evidence: `research.md`.

## Files this Ticket creates / modifies

| Path | Op | Step (decision) |
|------|----|-----------------|
| `src/hive/web/templates/landing.html` | modify | `:5` `viewport-fit=cover` (D5). `:27-47` drawer-toggle + `sr-only` name (D1/D8). `:55-62` close-x + `<aside>` ARIA (D8). `:16` `.drawer-scrim` + `aria-live` node (D8). `:359`/`:419` SSE attention wiring (D8, seeds 039). **NEW** drawer-control IIFE + **NEW** `visualViewport` keyboard IIFE near `:683` (D1/D2/D6/D8). `:684` resizer early-return + clear inline width (D7). `:894-906` `positionPopup` → `visualViewport` (D6). `:771,834,968,1089,1210` `mousedown`→`pointerdown` (D9). **Do not touch `640-660`** (Enter-to-send hotfix, D7). |
| `src/hive/web/static/landing.css` | modify | Replace `1423-1425` (`display:none`) with the fixed-overlay drawer (D1). Add `.drawer-scrim`, narrow-only 44×44 `.drawer-toggle`, hide the resizer at `<=900px` (D1/D2/D7). `viewport-fit` `env(safe-area-inset-*)` via `max()` on `.top-bar`/`.terminal-bar`/`.composer` (D5). **NEW** `@media (pointer:coarse)` 44px block with inset `::before` hit-boxes (D3). Move ~18 hover rules into one `@media (hover:hover)` block; keep `:active` in base (D4). **NEW** `prefers-reduced-motion`, `.sr-only`, `focus-visible` rings, global transparent tap-highlight + `touch-action:manipulation`. Raise `.top-bar`/`.terminal-bar` z-index (D1). |
| `docs/adr/0022-responsive-touch-shell-contract.md` | (already shipped) | The touch-shell contract for 038–040. |

No new files. No `/dashboard` JSX, no PWA manifest/SW, no decision API.

## Verification

Acceptance is only fully provable on the device — units/lint gate, the iPad
re-smoke confirms.

- `ruff check src/ tests/` **and** `ruff format --check src/ tests/` both green
  (separate CI gates — fixing lint does not fix format).
- Full `pytest -m "not integration"` green; confirm **real CI** green, not a
  scoped local run.
- Deploy: `git push` → `systemctl --user restart hive.service` → `journalctl
  --user -u hive.service -n 20` clean.
- Smoke from the **Tailscale IP** (`http://100.79.194.84:<port>/`), not loopback.
- **Real iPad, portrait (768px):** drawer opens via the top-bar toggle; composer
  + Send visible and usable; **scrim-tap AND close-x both dismiss**. (Core DoD.)
- **Real iPad, tap targets:** Send, Attach, help-chip, Allow/Deny, bells, tabs,
  participant-close, History/+New all comfortably ≥44px; desktop pixel-unchanged
  under a mouse.
- **Real iPad, keyboard up:** Shift-Enter newlines, plain Enter sends,
  predictive-text Return does **not** send; the just-sent message **and** the
  agent reply are visible (not behind the keyboard).
- **Real iPad, keyboard up:** autocomplete + help/bell/gate popups reachable, none
  clipped; tap a suggestion (`pointerdown` select); tap-outside dismisses.
- **a11y:** with a keyboard, Tab cannot leave the **open** drawer (`inert`); with
  the drawer **closed**, VoiceOver does not reach the hidden composer/approval
  buttons; `focus-visible` ring shows; focus returns to the toggle on close.
- **SSE attention:** a mode/vault request while **closed** shows an unread dot +
  `aria-label`, announces via `aria-live`, and opening scrolls to the newest bubble.
- **Real iPad, landscape (notch/home-indicator):** bars/drawer/composer clear the
  safe area; rotate landscape→portrait with the keyboard up → the drawer re-sizes
  (inline width cleared, `--kb` recomputed).
- `prefers-reduced-motion` ON: the drawer appears without the slide (state toggles).
- **Desktop regression `>900px`:** two-column layout, drag-resizer, and all hover
  lifts behave as before; bell/gate/help popups anchor identically.

## Open questions for the builder

- **Top-bar fit at 768px** — brand 220px + 4 tabs + pills + 2 bells + terminal +
  a 44px toggle, under `.shell{overflow:hidden}`. Safe default: hide
  `.terminal-btn` + the brand `v0.4` meta at narrow; keep both bells.
- **Scroll-lock target** — confirm on-device whether `<body>` scrolls at all
  (given `.shell{overflow:hidden}` + `.content-inner{overflow:auto}`). If not,
  `overflow:hidden` on `.content-inner` suffices and the `position:fixed`-body
  fallback is dead code.
- **`visualViewport.offsetTop` geometry** — when iPadOS auto-scrolls to reveal a
  bottom field, the `--kb` formula may need a second `rAF` re-read after the
  auto-scroll settles. Re-smoke with the field near the drawer bottom.

## Out of scope

- New observability widgets / chart redesigns.
- PWA install + service worker + push (040 / 041).
- The `/api/decision` endpoint + decision bubble (038) — though 038 will render
  *inside* this drawer and inherit its `inert` + `aria-live` wiring.
- Gesture shortcuts (swipe/long-press); a native `<dialog>`+`showModal()` refactor
  (recommended future follow-up, would delete most of D8's hand-rolled a11y code).
- Pointer-Events port of the drag-resizer (it's retired in the drawer state).

## Cross-cutting impact

- **ADR:** [0022](../../adr/0022-responsive-touch-shell-contract.md) — shipped with
  `design.md`. The touch-shell contract 038/039/040 build on.
- **`CONTEXT.md`:** no new glossary term (drawer/scrim/tap-target are generic).
- **README / DEPLOYMENT / ARCHITECTURE:** no change — 037 alters web UI, not
  deployment, topology, or the system map.
- **`INDEX.md`:** flip 037 → `in progress` (issue #200) now; → `done` at merge.
- **`CHANGELOG.md` + the `CLAUDE.md` sprint pointer:** at **S8 close**, not this PR.
- **Rebase note:** 037–040 all edit `src/hive/web`; 037 is do-first, the later PRs
  rebase.

## To build

Single PR, branch `ticket-037/touch-shell` (or via the run skill) following
`outline.md` steps 1–11; closes #200.
