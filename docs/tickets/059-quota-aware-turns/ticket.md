# 059 — Reliability: quota-aware turns

> **Backlog — re-grill when scheduled into a sprint.** Roadmap Phase 6.
> The #1 silent-death for a long unattended build.

## What

Detect **plan-quota exhaustion** at the turn layer, surface it as a *named*
outcome (not a generic stall), hold off the auto-bounce on a quota wall, and gate
the scheduler/dispatcher on `get_quota()` — pausing until `resets_at` instead of
flapping. Also: refresh the OAuth token so a multi-day run doesn't go blind.

## Why

At plan-quota 100% Claude Code writes no transcript entry → every turn dead-ends
in the 180s no-progress timeout → misclassified as a stall → auto-bounce flaps
the entity into ERROR with "cause unknown". A multi-day build **will** hit quota
and die silently (found in the 2026-06-30 research sweep).

## Acceptance

TBD at grilling.
