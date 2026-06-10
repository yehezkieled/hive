# 015 — Lead leaf engine: orchestrate leaf work via Claude Code Workflow

## What

Give Team Leads the ability to execute their scoped goal by driving the
Claude Code **Workflow** tool — deterministic fan-out / pipeline of
ephemeral agents — instead of spawning persistent Worker Entities. Requires
**relaxing the lead `Agent`/`Task` guard** (`lifecycle_manager.py:69-76`,
which today blocks those tools for maestro/lead) so a Lead can author and
launch a Workflow, and prompting the lead role JD to use it for leaf fan-out.

## Why

A Lead's job *is* fan-out: decompose, spread, collect, synthesise, report.
The Workflow tool does exactly that — deterministically, cheaply, in one
synthesis turn — and removes the fragile, token-heavy LLM-driven
worker-coordination dance (stalls, parse loops, role-discipline bugs). It
runs **async** (backgrounds, fits Hive's turn model: the Lead's turn
completes, a notification re-invokes it on completion) and supports per-agent
`isolation: 'worktree'` for safe parallel code work. Foundation for retiring
persistent Workers (018) and for the interaction-pattern library (Track 2).

## Acceptance

- The lead `Agent`/`Task` guard is relaxed enough for a Lead to invoke the
  Workflow tool, without re-opening uncontrolled Hive-Entity spawning.
- A Lead can take a scoped goal and run it as a Workflow (fan-out and/or
  pipeline), returning a synthesised result — proven in a hermetic test.
- The lead role JD prompts Workflow use for leaf fan-out.
- No regression to Maestro→Lead spawning (that stays Hive-native).
- `ruff` + `pytest -m "not integration"` green.

## Non-goals

- Migrating the existing `spawn_worker` dispatch (016) — this ticket adds the
  capability; 016 makes it the default path.
- Deleting the Worker entity (018).
- The interaction-pattern library / named workflows (Track 2).

## Open design questions (→ research/design)

- Does the Workflow tool function for a Lead with `Agent`/`Task` otherwise
  constrained, or does relaxing the guard re-enable the uncontrolled
  CC-subagent spawning the guard was added to prevent — and how do we
  re-fence that differently?
- **Nested worktrees:** a Lead already runs in a Hive worktree; how do
  `isolation: 'worktree'` agents nest cleanly given this session's observed
  worktree quirks?
- Turn-model interleave: does the existing background-notification → re-invoke
  path carry the Workflow result back to the Lead intact?

## Cross-cutting / ADR

Reverses the deliberate lead-guard decision — **ADR-worthy**. Declare any
reference-doc impact in `plan.md`.
