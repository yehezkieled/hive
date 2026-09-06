# 018 — Retire the persistent Worker entity

## What

Delete the persistent **Worker** entity type once nothing routes through
`spawn_worker` (016): remove `models/worker.py`, the Worker lifecycle, the
`spawn_worker` hive_action, the worker-specific dashboard rendering, its
persistence, and its tests. The actual cut of the "delete Workers" decision.

## Why

Removes the fragile leaf-coordination layer entirely — the biggest
reliability and cost win of the Workflow migration, and a large code
deletion. After 016, leaf work is always ephemeral Workflow agents; the
persistent Worker type is dead weight. (A steerable long leaf task is handled
by a persistent Lead.)

## Acceptance

- `models/worker.py`, `spawn_worker`, and all Worker-specific code / tests are
  removed; no reference remains in `dispatch.py`, the lifecycle, the
  dashboard, persistence, or `hive_actions`.
- The org model is now: persistent = Maestro / Lead, ephemeral = Workflow
  agents.
- `ruff` + full `pytest -m "not integration"` green; a maestro turn completes
  end-to-end with leaf work on Workflow.
- Glossary: `CONTEXT.md` "Worker" term updated — retired, or redefined as the
  ephemeral Workflow agent (decide in design).

## Non-goals

- The Workflow engine / migration (015 / 016 — prerequisites).
- The interaction-pattern library (Track 2).

## Risk

Large surface; **likely tails into S6**. The path is already drained by 016,
so this is removal, not behaviour change — (c) is the target end-state,
reached when this lands.

## Cross-cutting ✱

Touches `dispatch.py`, the dashboard, persistence, and the `CONTEXT.md`
glossary. Declare in `plan.md`.

## Blocked by

- 016 (the `spawn_worker` path must be drained first).
