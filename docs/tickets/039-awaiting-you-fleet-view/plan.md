# Plan — Ticket 039: "Awaiting-you" fleet view  (issue #202)

**Lane:** direct (one PR that closes #202). **Sprint:** 2026-Q2-S8.
Surfacing ticket: plumb a per-card `awaiting_you` boolean (maestro-rollup of
`awaiting_decision` OR gate-parked, own + leads) into the landing view model,
render it as a badge, and add a client-side "Waiting on me" filter. Decisions in
`design.md`; build order in `outline.md`.

## Files this Ticket creates / modifies

| Path | Op | Step |
|------|----|------|
| `src/hive/web/view_model.py` | modify | Add `_is_awaiting(pm, entity)` helper; set `awaiting_you` on the maestro card via a leads rollup (`f"{name}."` prefix over `process_manager.entities`); add the key to `_OTTER_STUB` (`:179-191`) and to `idle_list` rows (`:220-228`). |
| `src/hive/web/templates/_macros.html` | modify | `maestro_card` (`:71-105`): emit `.awaits` badge + `is-awaiting` root class when `m.awaiting_you`. Copy = `● you`. |
| `src/hive/web/templates/_partials/idle.html` | modify | Idle row (`:3-10`): same badge + `is-awaiting` (inline variant). |
| `src/hive/web/templates/landing.html` | modify | "Waiting on me" chip in the Active `.section-head__right` slot (`:165`, outside the hx-swap target); static empty-state line; extend the document-delegated handler (near `:661-671`) to toggle `body.show-awaiting-only` + `.is-pressed` and show/hide the empty state. |
| `src/hive/web/static/landing.css` | modify | Append near `:1020-1160`: `.awaits` (clone `.bell__count` `:202-218` + `a3-badge` pulse), `.chip-awaiting`/`.is-pressed` (clone `.composer__chip` `:633-646`, **≥44px**), and `body.show-awaiting-only .maestro-card:not(.is-awaiting)` / idle-row hide. **Do NOT edit the `@media` tail `:1420-1425`** (037's zone). |
| `tests/web/test_view_model.py` | create/modify | `awaiting_you` True for own-`awaiting_decision`, True for a gate-parked lead under the maestro, False otherwise; key present on `_OTTER_STUB` + idle rows. |

## Verification

- `ruff check src/ tests/ && ruff format --check src/ tests/`
- `pytest -m "not integration"` — **full** suite (a scoped run misses failures).
- Deployed **iPad re-smoke** (portrait + landscape), both `/` and `/dashboard`
  (shared `landing.css`): fire a real `request_decision` → badge ≤5s → chip
  isolates it → answer → clears ≤5s. A curl-200 is **not** sufficient (Safari
  mounts htmx/JS).

## Out of scope

- Lead/run cards as first-class nodes (the S9+ fleet board — non-goal).
- Mode/vault approvals on the badge (source C; stays on the existing bell).
- SSE-driven instant update (poll-only by design; SSE CLEAR fires no event).
- Web Push of these alerts (041).
- New approval *types* — only surfacing existing ones.

## Cross-cutting impact

- **None on reference docs.** No ADR (surfacing existing state via existing
  transport — no append-only decision); no `CONTEXT.md` (UI labels, not new
  domain terms); no `README`/`DEPLOYMENT`/`ARCHITECTURE` (no new route/runbook).
- **Land order:** independent of 038; **rebase onto 037** (shared `landing.css`
  `@media` tail + `landing.html` chrome) and merge after it. The fleet merges one
  PR at a time.

## To build

One branch `ticket-039/awaiting-you-fleet-view`, one PR that **closes #202**.
Build directly (you or a single agent) per `outline.md`'s bottom-up steps.
