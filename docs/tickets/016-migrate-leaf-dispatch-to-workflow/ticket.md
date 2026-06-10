# 016 — Migrate leaf dispatch: `spawn_worker` → Workflow

## What

Reroute all leaf-task execution from the `spawn_worker` path onto the Lead
Workflow engine (015), so a Lead's "do this work" produces a **Workflow run**
rather than spawned Worker Entities. Touches the dispatch / `hive_actions`
routing that today turns a lead's intent into `spawn_worker`.

## Why

Completes the swap of the leaf execution model and **drains the
`spawn_worker` path** — which must be empty before the Worker entity type can
be deleted (018). The cheaper / deterministic / reliable leaf path becomes
the default, not just an option.

## Acceptance

- A lead-scoped goal that previously spawned Workers now executes via Workflow
  end-to-end; `spawn_worker` is no longer invoked on the leaf path.
- Existing lead / maestro behaviour and messaging are otherwise unchanged.
- Tests covering leaf dispatch are repointed to the Workflow engine.
- `ruff` + `pytest -m "not integration"` green; a maestro→lead→leaf turn
  completes end-to-end.

## Non-goals

- Deleting the Worker class / lifecycle (018) — 016 only stops *using* it on
  the leaf path.
- 017's progress bridge (separate).

## Cross-cutting ✱

Heavily touches **`dispatch.py`** (1,353-LOC god object). The migration may
require, or pair with, a dispatch cleanup — declare the scope in `plan.md`.

## Blocked by

- 015 (the Lead Workflow engine).
