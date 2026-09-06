# 060 — Reliability: bounce/idle guard bundle

> **Backlog — re-grill when scheduled into a sprint.** Roadmap Phase 6.
> Cheap, known false-fire fixes for long unattended runs.

## What

A bundle of the concrete false-fire fixes from the 2026-06-30 sweep:
- exempt `awaiting_decision` entities from the idle reaper (today a parked
  non-maestro is auto-**killed** at 30 min — unrecoverable);
- set a waiting flag on **lead→maestro** escalation so a waiting lead is
  bounce-exempt (today only maestro→user sets it);
- widen / fail-safe the `workflow_active` liveness window (180s == the reader
  timeout → a slow-but-live Workflow can read inactive at timeout);
- bounded autonomous recovery from ERROR after give-up.

## Why

On a multi-day unattended run these known gaps false-kill or false-bounce
legitimately-waiting entities, or strand one permanently in ERROR.

## Acceptance

TBD at grilling.
