# 0013 — Retire Worker creation on all paths (lead, maestro, user)

**Status:** accepted (2026-06-12, Ticket 016)
**Builds on:** [0010](0010-leads-orchestrate-via-workflow.md) (leads
orchestrate leaf work via the Workflow tool)

## Context

ADR 0010 gave Team Leads the Workflow tool as the leaf engine but left
`spawn_worker` alive as a demoted-legacy mechanism, reachable three
ways: a lead's `hive_actions`, a maestro's `hive_actions` (under any of
its leads), and the user's `/worker spawn` Telegram command. The
persistent Worker carries most of Hive's reliability tax (wake
scheduling, message routing, lifecycle bugs), and Ticket 018 is
premised on "nothing routes through `spawn_worker`".

Three live entry points make that premise unprovable: prompts
re-advertise the verb to maestros every scheduler tick, models drift
back to verbs their prompts teach, and each regression is silent — a
Worker quietly exists while the dashboard is blind to leaf work until
Ticket 017 lands.

A second hazard tipped the decision against half-measures: a
permission denial on `spawn_worker` is log + audit + `continue` — no
reply to the actor. A lead-only deny without feedback would leave a
denied lead believing its worker exists, sync-waiting forever on a
report that never comes.

## Decision

Ticket 016 bans Worker **creation** everywhere, as one change:

1. `can_spawn_worker` denies all actors (lead and maestro arms
   removed).
2. The `spawn_worker` dispatcher branch replies to every denied actor
   via the existing `_reject_action` plumbing, naming the replacement
   (the Workflow tool) without inviting a retry.
3. The user-facing `/worker spawn` command is removed.
4. Every prompt surface that teaches the verb is trimmed: the lead JD
   legacy block (including the lead's `kill_entity` docs — nothing
   left to kill), the maestro JD verb docs, the scheduler facts
   prompt, and the kickoff text.

The org model becomes **Maestro → Leads → Workflow runs**. Persistent
capacity need = create another lead. "Drained" for 018 means
*mechanically zero ways to create a Worker*, not "expected zero
traffic". Pre-existing Workers live out their lives (`kill_entity`
still works at the code level until 018; stragglers are killed at
deploy).

018 then deletes the dead machinery: the Worker class and lifecycle,
the dispatcher branch, the verb parsing, `can_spawn_worker`, the
worker restore path, and mechanism-level tests.

## Alternatives considered

- **Prompt-only demotion** (status quo ante): relies on the model
  never regressing across compactions; regressions are silent and
  invisible until 017. Rejected.
- **Lead-only deny, maestro arm latent until 018**: keeps maestro
  Workers appearing under leads whose JD no longer documents managing
  them, and makes 018's precondition hope rather than proof. Rejected.
- **Hard deny without feedback**: strictly worse than drift — a
  guaranteed silent jam. Rejected; the feedback is inseparable from
  the deny.

## Consequences

- 018's precondition is provable from code + the
  `entity.spawn_worker_denied` audit stream, not inferred from prompt
  discipline.
- No escape hatch between 016 and 018: if the Workflow leaf path hits
  a production wall, the fallback is a one-commit revert. Accepted —
  a lead is itself a persistent agent, covering the long-lived-state
  use case.
- The old path's mechanical guarantees move to lead-JD rules (failure
  enumeration, bounded fan-out with distilled results, tag hygiene)
  and a worktree policy keyed to release granularity — shared-worktree
  single PR by default, per-agent isolation with per-slice PRs when
  slices are independently shippable.
- Leaf work is invisible between 016 and 017 (accepted sprint risk;
  017's bridge is the fix).
