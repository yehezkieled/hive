# Hive cut-over: retire the Maestro runtime, keep the Vault

## Status

Accepted, 2026-10-05. Carries out the cut-over [ADR 0030](0030-firstmate-implemented-in-hive.md)
deferred to its last milestone (M4). Supersedes the Maestro / Team Lead /
Worker parts of [ADR 0006](0006-god-object-breakup-composition.md) (the
`ProcessManager` collaborators that served them),
[ADR 0010](0010-leads-orchestrate-via-workflow.md),
[ADR 0013](0013-retire-worker-creation-all-paths.md),
[ADR 0016](0016-worktree-reconciliation-scope-and-orphan-policy.md),
[ADR 0017](0017-ownership-guard-pretooluse-hook.md),
[ADR 0018](0018-conversational-decision-channel.md),
[ADR 0019](0019-maestro-phase-confirmation-gate.md),
[ADR 0020](0020-interaction-patterns-as-jd-recipes.md) and
[ADR 0024](0024-decision-channel-entity-keyed.md). It also retires the
Telegram-as-required-runtime assumption of
[ADR 0029](0029-harness-pivot-headless-default-pty-fallback.md); that ADR's
harness registry, run modes and fallback rules are unchanged.

## Context

The gateway desk (`hive-gateway.service`, tailnet web UI over firstmate's
records) is the primary surface, and Hive has its own first mate. The Maestro
orchestrated work, owned Projects, ran a priority scheduler and fanned leaf work
out through Team Leads. firstmate now does that job, so the Entity runtime for it
is dead weight. The captain also wants Telegram demoted from required to an
optional backup ("move away from telegram, or put it as a backup — web +
tailscale instead").

Two things must not break: the desk, and the **Vault** — the one remaining
approval rail. A payment must stay approvable end to end, and the captain has not
yet set up the separate account/bank details a real provider needs.

## Decision

1. **Retire the Maestro, Team Lead, Team, Worker and Project concepts and
   their code**: models, spawn/kill/team actions, the priority scheduler, the
   Workflow watcher and progress store, git worktrees and their reconciliation,
   the ownership guard, the phase-confirmation gate, the decision channel, the
   `/team`, `/new`, `/org`, `/ship`, `/project`, `/eval` and `/priority`
   commands, and the local readline CLI. `hive_actions` shrinks to
   `request_mode_change` and `request_payment`.
2. **Keep a minimal Entity runtime for the Vault only**: `Entity`/`Vault`,
   `ProcessManager` with the lifecycle, approval, message-dispatch and wake
   collaborators, `HarnessRuntime` and the adapters, `EntityStore`, `VaultStore`
   and the `/approve` `/deny` `/vault` commands. The Vault is unchanged: off by
   default (`HIVE_VAULT_ENABLED`), stub provider only, spend caps as before. No
   real payment provider, account or bank detail is connected by this change.
3. **Telegram is optional.** The bridge starts only when `TELEGRAM_BOT_TOKEN` is
   set; with no token Hive no longer falls back to a blocking local CLI and
   everything else (Vault rail, notification dispatcher, legacy web app, health
   monitor) still runs. When configured it stays a backup channel for pings and
   for the typed `/approve` `/deny` `/vault` commands. It is not removed.
4. **The legacy `python -m hive` web app is kept**, trimmed to Entity-free pages
   (vault and mode-request approvals, status, cost, tasks, audit, push
   subscription). The desk does not yet cover vault/mode approvals; removing the
   legacy app waits for the desk's vault buttons (a separate change held for the
   captain). Typed `/approve` `/deny` `/vault` removal (T017) is deferred for the
   same reason.
5. **Forward migration `035_retire_maestro_runtime.sql`** deletes every
   `entities` row whose role is not `vault`, drops the `projects` table, and
   drops the Maestro/Lead columns from `entities` (`parent_name`, `team_name`,
   `worktree_path`, `task_id`, `awaiting_decision`, `confirmed_with_user`,
   `phase_confirm`, `last_decision_question`) and `idx_entities_parent`. The
   `entities` table itself stays because the Vault row lives in it. `messages`,
   `tasks`, `token_usage`, `audit_log`, `vault_actions`, `mode_requests`,
   `blueprints` and `attachments` are untouched.

## Consequences

- **Data loss, one-way.** Persisted Maestro/Lead/Worker rows, Project ownership
  records and the retired columns are gone after the migration and are not
  restorable except from a database backup. Take one before deploying. Orphan
  worktree directories under `worktrees/` are no longer swept; they hold no
  Hive state and can be removed by hand.
- Mode-elevation requests now always go to the user (there is no parent Entity
  to approve them).
- Peer messaging, Telegram `/m:<entity>` and web chat only reach the Vault (or
  any restored non-retired entity); plain text with no target is answered with a
  pointer to the desk.
- Role settings other than `vault` (`HIVE_HARNESS_ORDER_LEAD`,
  `HIVE_CLAUDE_MODEL_MAESTRO`, …) are ignored. `HIVE_DEFAULT_MAESTRO` and the
  priority-scheduler variables are gone.
- The glossary terms Entity-as-Maestro/Lead, Team, Leaf agent, Workflow run,
  Interaction pattern, Phase-confirmation gate, Ownership guard and Worktree
  reconciliation retire (see `CONTEXT.md`).
