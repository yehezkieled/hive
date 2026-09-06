# 016 — Research

What the code actually says, keyed to `questions.md`. Produced from a
5-reader + adversarial-critic fan-out (2026-06-12), with the
load-bearing refs re-verified by hand in this session. Facts only —
decisions live in `design.md`.

## Q0 — Confirmed: there is no mechanical dispatch to reroute

A lead emits `spawn_worker` only because prompts tell it to. The
Workflow path is an in-turn CC tool call — no `hive_actions` verb, no
dispatcher branch. "Migrating leaf dispatch" therefore means editing
prompts and (optionally) denying the old verb, not repointing code.

The full legacy path:

```
Lead turn ends with <hive_actions> spawn_worker
  ▼
parse_actions                       bus/actions.py:280-298
  ▼
_handle_actions branch              process/message_dispatcher.py:471-535
  ├─ missing-lead inference          :477-492 (lead defaults to itself;
  │                                   maestro without `lead` → denied)
  ├─ can_spawn_worker                bus/permissions.py:136-145
  ├─ autospawn rate limit            :502-515 (scheduler.can_autospawn)
  ▼
ProcessManager.spawn_worker (facade) process/manager.py:371-379
  ▼
LifecycleManager.spawn_worker        process/lifecycle_manager.py:352-440
  → registers IDLE Worker `<lead>.<wN>`, creates per-worker worktree
    (:394-399), persists + routes, audits entity.spawn_worker (:434-438)
  ▼
detached _auto_kickoff               message_dispatcher.py:563-568
  → wake_scheduler.py:56 sends _SPAWN_KICKOFF_TEXT
```

The prompts that advertise the verb (the *actual* dispatch surface):

| Where | What it says | Ref |
|-------|--------------|-----|
| Lead JD legacy section | "`spawn_worker` still works, but it is the **legacy** leaf mechanism … reach for a persistent worker only when your maestro explicitly asks" | `personalities/role-lead.md:98-143` |
| Kickoff text | "Spawn workers if the work warrants subdivision" — sent to **every** spawned entity, leads included | `process/wake_scheduler.py:25-30` |
| Scheduler facts prompt | "Decide: emit <hive_actions> with spawn_team / spawn_worker / kill_entity" — maestro-facing, every eval tick | `process/scheduler.py:8,197` |
| Maestro JD | documents `spawn_worker` (maestro or lead) with the `lead` field | `personalities/role-maestro.md:100-101` |

## Q1/Q2 — Enforcement mechanics

- `can_spawn_worker` (`bus/permissions.py:136-145`): maestro arm —
  spawn under any lead in its org (prefix check); lead arm — only under
  itself; everyone else `False`. Cutting the lead arm is a 2-line
  change.
- **Denial is silent to the actor.** The `spawn_worker` branch logs,
  audits `entity.spawn_worker_denied`, and `continue`s
  (`message_dispatcher.py:493-501`). The `_reject_action` feedback path
  (`:687-715`) — the "[action rejected]" resend note the lead JD
  promises — is invoked **only** for `message` actions (`:301`, `:325`).
  A hard-denied lead today would get no signal and could sync-wait
  forever on a phantom worker's report.
- Parse errors are different: malformed `spawn_worker` JSON does message
  the actor back (the JD's escaping note relies on this), so the
  feedback asymmetry is specific to permission/rate-limit denials.

## Q3 — Drain inventory (who keeps the verb alive after a lead-only cut)

| Caller | Path | Nature |
|--------|------|--------|
| Maestro `hive_actions` | `permissions.py:140-142`, `role-maestro.md:100-101` | autonomous |
| Scheduler nudge | `scheduler.py:197` advertises the verb to maestros every tick | prompt |
| Kickoff text | `wake_scheduler.py:25-30` tells every spawnee to spawn workers | prompt |
| User command | `/worker spawn` → `commands/dispatch.py:701` | manual |

018's precondition is "nothing routes through `spawn_worker`"; 016's
acceptance is "not invoked on the leaf path". The gap is exactly the
four rows above.

## Q4 — Lead JD edit range

The legacy material spans `personalities/role-lead.md:98-143`:
`## Legacy: persistent workers` (98-122) — **containing the lead's only
`kill_entity` documentation (~111-113)** — then `### Spawn Template
(legacy)` (~124-137) and the spawn-specific JSON-escaping note
(~139-143). `tests/test_role_jd.py:103` asserts `"spawn_worker" in
text` with the literal annotation `# demoted legacy, removed by 016`.
Maestros can still spawn workers *under a lead* until 018, and that
lead remains their manager (kickoff target, escalation rung), so
worker-management text cannot vanish wholesale.

## Q5/Q6 — Reporting & failure semantics (what Workers have, Workflow lacks)

- **No `finish_task` verb exists** (zero grep hits in src/ and docs/).
  Worker success = a plain `message` to `"parent"` (alias resolved at
  `message_dispatcher.py:681-685`), delivered by wake-on-inbound
  (`wake_scheduler.py:125-178`) and prepended to the lead's next prompt
  (`message_dispatcher.py:108-116`). Task rows are completed only by the
  user's `/task done` (`commands/dispatch.py:505-530`).
- **Failure** = `report_failure` verb (`actions.py:239-260`) →
  `handle_task_failure` (`message_dispatcher.py:405-420` →
  `approval_handler.py:623`): retries the assignee up to `max_retries`,
  then escalates one rung (worker→lead→maestro→user,
  `approval_handler.py:609-621`, routed `:700-702`). The Workflow path
  has no equivalent — a failed leaf exists only in the lead's synthesis.
- Task rows (`models/task.py:24-39`, `bus/task_store.py:77-128`) feed
  the dashboard CFD/sankey (`web/view_model.py:410-496`) and
  failure-scatter (`:690-779`). Workflow runs write none.
- Side effects of `spawn_worker` a Workflow run does not replicate:
  org-tree registration (`lifecycle_manager.py:401-422` →
  dashboard cards/matrix, `web/view_model.py:99-102,599-606`),
  persistence for crash-restore (`bus/entity_store.py:40-75`,
  `manager.py:543-598`), per-entity token-usage rows, audit entries.
  ADR 0010 already accepts the token undercount (only the final
  assistant entry bills to the lead).

## Q7 — Caps bound only the old path

`lead.max_workers` (`lifecycle_manager.py:376-379`) and the autospawn
rate limit (`message_dispatcher.py:502-515`,
`config.py:209 AUTONOMOUS_SPAWN_LIMIT`) gate `spawn_worker`/`spawn_team`
only. Workflow width and `TaskOutput` payload size are mechanically
uncapped — bounded by JD prose alone. The rate-limit machinery is shared
with `spawn_team`, so draining `spawn_worker` must not strip it.
QuotaMonitor polls and alerts only — nothing gates turns on quota
(`runtime/quota_monitor.py:59-60`); a quota wall mid-run now fails one
long sync-wait turn instead of individual worker turns.

## Q8 — Tag hygiene

`TaskOutput` payloads land verbatim in the lead's transcript. A literal
`<hive_actions>` block in leaf output (or quoted in the synthesis) trips
the nested-tag rejection (established parse-loop failure). The lead JD's
existing tag-hygiene warnings cover only the lead's own emissions.

## Q9 — Visibility window

Until 017's bridge lands, a Workflow run produces no org-tree node, no
dashboard row, no Telegram ping — the lead just looks busy. 023 D4's
turn-end inbox check (`message_dispatcher.py:245-254`) drains queued
mail only after the sync-wait turn ends; ADR 0010 defers mid-run
steering to S6. Idle-kill already exempts busy adapters
(`lifecycle_manager.py:630-636`, fixed by 015), so long sync-waits are
safe from the reaper.

## Q10 — Leaf-worktree cleanup (inherited from 015)

`015/design.md:103-106` (verified): "Cleanup of **changed** leaf-agent
worktrees (commit → PR → merge → remove) is the full-dispatch concern →
**handed to 016**." CC-created `isolation:'worktree'` worktrees are
siblings on fresh branches outside `WorktreeManager`'s bookkeeping
(`process/worktree.py:44-48` never deletes branches); unchanged ones
auto-remove. The floor itself is live: leads spawn with a dedicated
worktree cwd (`lifecycle_manager.py:311-318`, lazy re-provision
`:254-262`), so even non-isolated leaf agents inherit the lead's
worktree, never the live checkout (023).

## Q11 — Test inventory

| Test | Covers | Ref |
|------|--------|-----|
| `tests/test_role_jd.py:103` | JD contract — literally annotated "removed by 016" | verified |
| `tests/test_process_manager.py:957-1041` | lead/maestro autonomous spawn | reader-verified |
| `tests/test_process_manager.py:1194-1207` | kickoff | reader-verified |
| `tests/process/test_message_dispatcher.py:422-438` | kickoff task tracking | reader-verified |
| `tests/integration/test_lead_worker_roundtrip.py` | worker→lead `hive_actions` comms + `peer_message_sent` audit — **NOT marked integration** (`:21-23`, verified); runs in the normal `-m "not integration"` gate | verified |

Mechanism-level tests (lifecycle, actions parsing, permissions' maestro
arm) are 018's to retire. Deleting tests interacts with the Ticket-011
CI coverage floor. `manager.py:26` re-exports `can_spawn_worker` for
back-compat; check import sites before touching re-exports.

## Why 026 mattered here (context, no action)

026 replaced the 500 ms quiescence heuristic with the `turn_duration`
sentinel. Without it, every long Workflow turn ending in a synthesis
(guaranteed tool-gaps before the final message) risked mid-turn
acceptance that silently dropped the lead's `hive_actions`. Done and
live-verified (#104–#105) — 016's "end-to-end on deployed code"
acceptance is meetable.
