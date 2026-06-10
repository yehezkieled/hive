# ADR 0009 — Leads orchestrate leaf work via Claude Code Workflow

- **Status:** Accepted
- **Date:** 2026-06-10
- **Ticket:** [015](../tickets/015-lead-workflow-leaf-engine/)

## Context

A Team Lead's job is fan-out: decompose a goal, spread the pieces, collect and
synthesize, report up. Hive does this today by having the Lead emit
`spawn_worker` `hive_actions` that create **persistent Worker Entities** — each
a full PTY session with Hive identity, a router queue, and a lifecycle. That
machinery is the bulk of Hive's reliability tax: stalls, parse loops,
wake-budget throttling on fan-in, and role-discipline bugs.

Claude Code's **Workflow** tool does the same fan-out deterministically and
cheaply — ephemeral agents, optional per-agent worktree isolation, results
returned in one synthesis — and the pinned binary (2.1.170) already exposes it.
[`research.md`](../tickets/015-lead-workflow-leaf-engine/research.md) corrected
three load-bearing assumptions before this decision:

1. **The guard never blocked `Workflow`.** The lead/maestro deny set
   (`Agent Task … TaskOutput TaskStop`) contains no `Workflow`; a live probe
   confirmed a lead-flagged session lists it. The framing "relax the guard to
   allow Workflow" was wrong — what the guard blocks that we *need* is
   `TaskOutput`/`TaskStop`, the sync-wait + cancel verbs, swept in under a
   "TodoWrite-family" label in commit `3586dfa` with **no recorded rationale**.
2. **A Lead runs in the live checkout, not a worktree.** Leads spawn with
   `cwd=None` → the service's `WorkingDirectory` = the main repo `src/` the
   deployed service imports. Leaf agents that write files would write into the
   running service's own source.
3. **The guard is conditional and non-persisted** — written only when a lead is
   spawned with `display_name`+`personality`, and lost entirely on a service
   restart (tool lists aren't persisted).

The original `Agent`/`Task` ban was never captured in an ADR; this is its first
written record.

## Decision

A Lead executes leaf work by driving the **Workflow** tool, and Hive adapts its
turn model and spawn policy to make that safe:

- **Carry-back = sync-wait (one Turn).** The Lead launches the Workflow, then
  **blocks on `TaskOutput`** in the same Turn, synthesizes the returned results
  in-context, and emits its report. The Turn never ends until the run finishes,
  so there is no self-invoked "spontaneous turn" for Hive's transcript reader to
  lose or mis-attribute. Rejected alternatives: a park-and-re-await *gate*
  (observably identical, more machinery) and a *persistent watcher as the result
  path* (releases the adapter lock → Hive believes the Lead is idle while its
  session is busy → mid-run messages collide; its only unique gain, a free slot
  for mid-run steering, is S6 scope and unsafe today). Progress **visibility** is
  separable and ships in Ticket 017 as a **read-only** transcript watcher that
  never touches the lock.
- **Fencing.** Prune `TaskOutput`/`TaskStop` from the **lead** deny set (sync-
  wait needs them); add `Workflow` to the **maestro** deny set (keep the chain
  Maestro → Lead → Workflow; a Maestro fanning out itself re-creates "the org
  never grows" one level up). `Agent`/`Task` stay denied for both — **Workflow
  is the only sanctioned fan-out**.
- **Policy in code, not markdown.** A pure `role_tool_denylist(role)` becomes
  the authoritative source, merged at `_adapter_config_from_entity` (runs on
  every spawn, restart included). The auto-personality `## Tools` block is no
  longer written; markdown `## Tools` survives only as a per-Entity override.
- **Worktree floor.** Leads spawn with a dedicated **worktree cwd**, so leaf
  agents — even non-isolated ones — never write to the live checkout; the JD
  directs `isolation:'worktree'` per file-mutating agent for clean parallel
  writes (empirically these land as sibling worktrees on fresh branches, no
  nesting collision).
- **Idle-kill** exempts any Entity with a **turn in flight**, not only `GATED`
  ones, so a long sync-wait is not reaped mid-run.
- **JD reframe.** `role-lead.md` is rewritten in place to teach
  Workflow-as-fan-out and stop treating ephemeral in-session agents as
  forbidden; `spawn_worker` is demoted but left working for the 016 transition.

## Consequences

- **Positive:** the reliability tax of persistent Workers leaves the leaf path
  (no router/wake/parse machinery between a Lead and its leaf results);
  deterministic, cheaper fan-out; the `CONTEXT.md` definition of **Turn** is
  preserved exactly (one prompt → one response); two latent bugs are fixed in
  passing (the restart-evaporating guard, and long turns being reap-eligible);
  the role tool-policy becomes a single unit-testable function.
- **Live-fleet behaviour change (accepted):** existing Maestros gain the
  `Workflow` denial and now keep their guard across restarts (previously
  silently dropped). Treated as a bug fix, not a regression.
- **Negative / accepted:** the Lead's adapter lock is held for the whole run, so
  a message *to that Lead* queues until the Turn ends — mid-run steering is
  deferred to S6, by design. Token usage undercounts Workflow-heavy turns (only
  the final assistant entry is recorded) — a pre-existing limitation left
  unfixed here. Leaf visibility regresses to "Lead busy → Lead done" until
  Ticket 017's read-only watcher lands — the sprint's intended 015→017
  sequencing.
- **Reversibility:** the fencing and JD changes are cheap to revert; the
  sync-wait turn model and worktree floor are the load-bearing, harder-to-
  reverse pieces — hence this ADR. The persistent Worker type is **not** deleted
  here (016 drains `spawn_worker`, 018 removes the type), so the old path
  remains a fallback during the migration window.
