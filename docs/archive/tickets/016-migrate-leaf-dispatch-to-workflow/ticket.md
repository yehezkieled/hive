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

*Corrected 2026-06-12 — the original note pointed at `dispatch.py` (the
1,353-LOC god object); the sprint's 2026-06-11 finding stands:* the
autonomous leaf path lives in **`message_dispatcher.py:471-535`** (the
`spawn_worker` hive_actions branch) and the **prompts that advertise the
verb** — `role-lead.md` (legacy worker section), the kickoff text
(`wake_scheduler.py`), and the scheduler facts prompt (`scheduler.py`).
`dispatch.py` holds only the user-facing `/worker spawn` command
(`:701`) — that is 018's territory, not 016's. The god-object cleanup
stays parked unless 018 forces it. Declare the final scope in `plan.md`.

## Blocked by

- 015 (the Lead Workflow engine) — ✅ done.
- 023 (worktree floor active in production; don't widen the live-checkout
  hazard) — ✅ done. *Added 2026-06-12; gated the sprint DoD line.*
- 026 (turn-boundary sentinel; fires on every long Workflow turn ending in
  a synthesis) — ✅ done. *Added 2026-06-12; same DoD line.*
