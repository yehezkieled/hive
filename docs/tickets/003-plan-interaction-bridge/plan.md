# Plan — interactive-gate bridge

The actionable plan. Structure comes from [`outline.md`](outline.md);
decisions from [`design.md`](design.md) and
[ADR 0004](../../adr/0004-interactive-gate-hold-and-inject.md). GitHub is
the work queue; **this table is the source of truth that travels with
the repo.**

## Issues

| # | Slice | Issue | Type | Blocked by |
|---|-------|-------|------|------------|
| 1 | Plan-gate hold-and-inject round-trip (**spine**) | [#22](https://github.com/yehezkieled/hive/issues/22) | AFK | — |
| 2 | `AskUserQuestion` gate round-trip | [#23](https://github.com/yehezkieled/hive/issues/23) | AFK | #22 |
| 3 | Web-dashboard surface for gates | [#24](https://github.com/yehezkieled/hive/issues/24) | AFK | #22 |
| 4 | No-answer nudge for parked gates | [#25](https://github.com/yehezkieled/hive/issues/25) | AFK | #22 |
| 5 | Permission-prompt gate (capture + verify + implement) | [#26](https://github.com/yehezkieled/hive/issues/26) | **HITL** | #22 |
| 6 | Restart-while-parked recovery | [#27](https://github.com/yehezkieled/hive/issues/27) | AFK | #22 |

All labelled `ready-for-agent`. No parent/PRD issue — this ledger plus
`design.md` are the spec (run-ticket default).

## Sequencing

```
#22 spine  ─┬─▶ #23 AskUserQuestion
            ├─▶ #24 web surface
            ├─▶ #25 nudge
            ├─▶ #26 permission (HITL — needs a captured prompt)
            └─▶ #27 restart recovery
```

The spine (#22) lands the GATED state, GateDetector, GateCoordinator,
doorbell, PtySession injection, and the `/approve` round-trip. Once it
merges, #23–#27 fan out in parallel — they reuse the spine and touch
mostly distinct seams.

#26 is HITL because the permission-prompt transcript shape is unverified
(design.md open choice #4): a human must trigger and capture a real
prompt before the detector can be built — or the slice is deferred with
findings recorded.

## Open implementation choices (decide in-slice)

Carried from [`outline.md`](outline.md) → "Decisions deferred":

1. New approval `kind` vs. reuse `mode_request` rows — **lean: add a
   `kind`** (#22).
2. Exact keypress per gate (plan menu may be 3 rows) — lives in
   `KeystrokePlanner` (#22 for plan, #23 for ask, #26 for permission).
3. Restart-while-parked recovery strategy (#27).
4. Permission-prompt transcript shape — capture first (#26).
5. Nudge interval (default ~60 min, tunable) (#25).

## Reference-doc impact

- `CONTEXT.md` — **done** (Interactive gate term, committed this ticket).
- `docs/adr/0004-…` — **done** (committed this ticket).
- `README.md` — **conditional.** If the spine (#22) surfaces the new
  `GATED`/`WAITING` state to users (Telegram/web), add a short note.
  **If so this becomes a cross-cutting Ticket** and the README edit must
  be declared in the implementing slice's PR.

## Definition of done

Mirrors the Sprint `2026-Q2-S2` DoD:

- A maestro in `plan` mode completes a Telegram turn without hanging:
  the plan reaches the user; the turn proceeds on approval or parks
  cleanly (#22).
- An `AskUserQuestion` mid-turn no longer hangs to the 180s timeout
  (#23).
- No screen-scraping introduced; gate detection reads the transcript
  (#22, #23, #26).
- Ticket 003 marked done in `docs/tickets/INDEX.md` once #22–#27 land
  (or #26 is explicitly deferred with findings).
