# 039 — "Awaiting-you" fleet view (attention router)

> Rank #1 in the competitor scan, and cheap — the data already exists in
> `view_model`. The scarcest resource for one dev driving a fleet is *which run
> needs me*.

## What

Surface "blocked on the user" as a first-class, glanceable signal: an unread
**"awaiting-you" badge** on each run-card / org-tree node whose entity is parked
waiting on a human (interactive gate, 029 decision, mode/vault approval), plus a
**"waiting-on-me" filter chip** that narrows the primary view to just those.
Today this state is known internally but surfaced only via separate bell/gate
popups.

## Why

Under "loop engineering" (human rarely in the loop), the app's job is to route
your scarce attention to the few runs that actually need you — across Hive's
multi-level hierarchy (Maestro → Lead → leaf Workflow run), which no flat
competitor covers. The data is already computed in `view_model`; this is a
surfacing ticket, not new backend logic.

## Acceptance

- Each entity/run blocked on the user shows an unread "awaiting-you" badge on its
  card / org node.
- A "waiting-on-me" filter chip narrows the list to only those.
- Badge clears when the decision/approval is answered.
- Works across the hierarchy (maestro + lead + run levels).
- Tested against `view_model` pending-approval / `awaiting_decision` state.

## Non-goals

- Promoting run-cards to a full kanban "primary fleet board" — larger, S9+.
- Push delivery of these alerts (041).
- New approval *types* — only surfacing existing ones.

## Notes

Mostly `src/hive/web/view_model.py` (state already computed) + templates / macros
(`_macros.html`, `maestro_card`). Pairs naturally with 037's touch shell.
