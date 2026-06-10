# 015 — Design (chosen approach)

Seeded by [`ticket.md`](ticket.md); grounded in [`research.md`](research.md);
decision recorded in [ADR 0009](../../adr/0009-leads-orchestrate-via-workflow.md).

## Decision in one line

A Team Lead executes leaf work by driving the Claude Code **Workflow** tool
inside **one long Turn** (launch → block on `TaskOutput` → synthesize → report),
running in **its own git worktree** so leaf edits never touch the live service.
The "guard reversal" is narrower than the ticket implied — `Workflow` was never
blocked; we prune two `Task*` verbs, deny `Workflow` to Maestros, and move the
whole role tool-policy from leaky markdown into code.

## The eight decisions

### Q1 — Carry-back = sync-wait (option A)

The Lead's leaf run is **one Turn that never ends until the run finishes**:
`Workflow(...)` to launch, then **blocking `TaskOutput`** to wait in-context,
then synthesize the returned results and emit the `hive_actions` report.

Rejected: **B (gate/park)** — observably identical to A from outside but adds
park + "progress-vs-done" detection for nothing; **C (persistent watcher as the
result path)** — releases the adapter lock so Hive thinks the Lead is idle while
its session is busy → a mid-run message collides with the running Workflow. C's
*only* unique gain is a free Lead slot for mid-run steering, which is **S6
scope** and currently unsafe.

Why A: it keeps the `CONTEXT.md` definition of **Turn** literally true (one
prompt, one response), gives the best synthesis (all partials in working
memory), and adds the least machinery. Progress **visibility** — the thing that
made C tempting — is **separable**: a *read-only* watcher (Ticket 017) tails the
same transcript and reports count/phase to the dashboard **without** releasing
the lock, so we get the clarity with none of C's desync. 015 ships the engine;
017 adds the eyes.

```
015 = ENGINE   Lead runs Workflow, result carries back via A. Lock HELD.
017 = EYES     read-only watcher → dashboard + Telegram. OBSERVES only, never
               injects, never routes the result, never unlocks.
```

### Q2a — Prune `TaskOutput` + `TaskStop` (forced by A)

Sync-wait needs `TaskOutput` (block-wait) and `TaskStop` (cancel a runaway /
required before a resume). Both are denied today → remove them from the **lead**
deny set. Keep `Agent Task ExitPlanMode TodoWrite TaskCreate TaskUpdate
TaskList TaskGet` denied (anti-subagent discipline holds; under A the `task_id`
stays in-context so `TaskList`/`TaskGet` aren't needed — widen later only if a
real need appears).

### Q2b — Deny `Workflow` to Maestros

Today's shared deny set leaves `Workflow` open to Maestros too. A Maestro
fanning out leaf work itself bypasses the Lead layer — the same "the org never
grows" failure the original guard fixed, one level up. Add `Workflow` to the
**maestro** deny set so the chain stays **Maestro → Lead → Workflow**. (No
escape hatch — "spin up a Lead" is cheap; role-discipline drift is a named
sprint risk.)

### Q2c — Move role tool-policy from markdown → code

Fixes both guard holes (research §3) at the root. A new pure function
`role_tool_denylist(role) -> list[str]` becomes the authoritative source, merged
in `_adapter_config_from_entity` — which runs on **every** spawn, restart
included. `_render_auto_personality` stops writing the `## Tools` block; the
personality `## Tools` markdown survives only as a **per-Entity override**.

```
role_tool_denylist(role):
  lead    → [Agent, Task, ExitPlanMode, TodoWrite, TaskCreate, TaskUpdate,
             TaskList, TaskGet]                 # TaskOutput/TaskStop pruned (Q2a)
  maestro → [Agent, Task, ExitPlanMode, TodoWrite, TaskCreate, TaskUpdate,
             TaskList, TaskGet, TaskOutput, TaskStop, Workflow]   # +Workflow (Q2b)
  worker  → []                                   # unchanged; retired in 018
```

Live-fleet behaviour change to acknowledge in the ADR: existing Maestros gain
the `Workflow` denial **and** keep their guard across restarts (previously
silently lost). Treated as a bug fix.

### Q3 — Leaf agents run off the live checkout: worktree floor + isolation

Two defenses, in depth (research §1-2):

- **Floor (enforceable, 015):** give the **Lead** a dedicated worktree cwd
  (Hive creates it, as it did for Workers) instead of `cwd=None`. Now even a
  *non-isolated* leaf agent inherits the Lead's worktree, **never** the live
  `src/`. This is the only code-enforceable guarantee — Hive owns the cwd.
- **Per-agent (prompt-guided, JD):** the lead JD instructs
  `isolation:'worktree'` for file-mutating fan-out → clean parallel writes.
  Empirically these land as **sibling** worktrees on fresh branches (research
  §"Nested worktrees"), so no nesting collision.

| Lead cwd | Agent isolated | `Edit` writes to | Live service |
|----------|----------------|------------------|--------------|
| main | no | live `src/` | **corrupted** ✗ |
| main | yes | sibling worktree | safe *if never forgotten* |
| **worktree** | no | Lead's worktree | **safe** ✓ |
| **worktree** ✅ | **yes** | per-agent worktree | **safe** ✓ |

Cleanup: the **Lead's** worktree is removed on Lead kill (reuse
`WorktreeManager`). Cleanup of **changed** leaf-agent worktrees (commit → PR →
merge → remove) is the full-dispatch concern → **handed to 016**; 015's hermetic
test never mutates a live tree, and unchanged agent worktrees auto-remove.

### Q4 — Idle-kill exempts any in-flight turn

A 20-minute sync-wait would look idle (`last_activity_at` stamped once per turn;
only `GATED` exempt) → the Lead gets reaped mid-run, orphaning the Workflow.
**Generalise the exemption: skip any Entity with a turn in flight** (adapter
lock held / busy flag). This is the correct invariant regardless of Workflow and
also closes a pre-existing latent bug (any long turn is reap-eligible today).

### Q5 — Reframe the lead JD (edit in place)

The Agent/Task ban's rationale ("ephemeral, no Hive identity, gone when the
session ends") **describes a Workflow agent** — leave it and the Lead refuses
the new tool. Teach a new model: *leaf work is a Workflow run; ephemeral
in-session agents are now correct for leaf tasks; fan out via `Workflow`, not
raw `Agent`/`Task`* (consistent with the denylist — Workflow allowed, Agent/Task
still denied). **Edit `role-lead.md` in place** (no new prompt block, to keep
the "exactly 3 blocks" test true); **demote** `spawn_worker` (keep it
mechanically working for the 015→016 coexistence window, stop instructing it).
`spawn_worker`'s removal from the lead + maestro JDs is **016**; the worker JD is
**018**.

### Q6 — Hermetic test proves the seam; the fan-out is a live smoke test

Running a real Workflow needs the live binary + subprocesses — not hermetic.
Draw the line at Hive's side of the seam:

- **Hermetic (CI):** `role_tool_denylist(role)` (lead pruned, maestro denied
  Workflow); spawn sets the Lead cwd to a worktree; lead JD contains Workflow
  guidance / no longer instructs spawn_worker; idle-kill skips an in-flight
  entity; the reader does **not** accept while the last entry has an unanswered
  `tool_use`.
- **Live smoke (deployed, not CI):** an actual maestro→lead→Workflow fan-out
  end-to-end — the sprint DoD's "a maestro turn completes end-to-end."

Two **reader hardenings** required by A (fold into the seam tests): (1) don't
accept a turn while the last assistant entry has an unanswered `tool_use`
(reuse `GateDetector`'s existing tool_use/tool_result pairing); (2) make the
180s timeout count **no-progress** (reset while a tool is pending or the
transcript moves), so a 30-min fan-out doesn't trip a wall-clock limit.

### Q7 — One ADR (0009)

Carry-back, fencing, worktree floor, and idle-kill are all mechanisms of one
decision — "Leads orchestrate leaf work via in-session Workflow agents instead
of persistent Workers." One ADR keeps the rationale whole; since the original
guard was never ADR'd, 0009 is also its first written record.

### Q8 — Usage undercount: known limitation, not fixed here

Only the final assistant entry's usage is recorded per turn, so Workflow-heavy
turns undercount tokens. **Pre-existing** (affects any heavy multi-step turn),
so fixing it (sum across entries) is orthogonal with its own risk. Flagged as a
known limitation + candidate follow-up; **out of scope** for 015.

## Glossary impact

No new `CONTEXT.md` term in 015 — "Turn" is preserved exactly by choosing A, and
redefining/retiring "Worker" is **018's** call. (Noting it here so the omission
is deliberate, not forgotten.)

## Out of scope (restated)

Migrating `spawn_worker` (016); deleting the Worker entity (018); the read-only
progress watcher / dashboard (017); steering a running Workflow (S6); the usage
fix (Q8); the interaction-pattern library (Track 2).
