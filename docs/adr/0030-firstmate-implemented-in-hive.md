# 0030 — Firstmate is implemented in Hive: one first mate, project lenses, a website over Tailscale

## Status

Accepted, 2026-10-02. Supersedes the Entity-runtime ADRs listed under
[Superseded ADRs](#superseded-adrs). Takes effect at the cut-over (step 10 of the
build order below); until then the existing runtime stays in the code. ADRs
0022, 0023, 0026 and 0027 stay valid.

## Context

Hive and firstmate overlap. Hive runs a Maestro / Team Lead / Leaf-agent runtime
with its own PTY and headless adapters, ownership guard, worktree manager and
Postgres entity tables. Firstmate already runs the same job on the owner's PC:
crews in isolated worktrees, the no-mistakes review gate, captain-held
decisions, a backlog, and nine verified harnesses. What Hive has that firstmate
lacks is the front door: the website, PWA, Web Push and the Delegator's Desk
design ([ADR 0027](0027-web-delegators-desk.md)).

The harness pivot ([ADR 0029](0029-harness-pivot-headless-default-pty-fallback.md))
exposed the cost of owning a runtime. With a non-Claude harness first, no project
Maestro is fenced: the ownership guard ([ADR 0017](0017-ownership-guard-pretooluse-hook.md)),
the role tool denylist and the MCP config are Claude Code features. Firstmate's
isolation (one worktree per task of one project; the primary never writes a
project) does not depend on a harness hook.

The owner's words: "implement firstmate in hive instead", with the interface on a
website reached over Tailscale; "Ok D / And build"; "Yup hive gets the first
mate"; tickets must stay in sync between the first mate and Hive's first mate and
be editable from both sides, "Else the tickets wont get finished"; Maestros stay
Claude first with Pi as the fallback when Claude cannot be reached; the website is
hosted on the PC over Tailscale for iPad and phone; "If it is possible to go into
production, please do that".

## Decision

**Firstmate is implemented in Hive. Hive becomes the front door; firstmate stays
the brain.** Design option D from the design report
(`hive-fm-design`, kept in the firstmate home):

1. **One first mate across all projects.** It is the owner's single point of
   contact for software work and owns the backlog. Hive does not run its own
   Entities for this.
2. **Project pages are lenses.** A project is a filtered view of the first mate's
   backlog, crews, parked work, landed PRs and reports, not a separate
   supervisor. The chat-target chip on the page says who answers.
3. **A second mate per project only when it earns one.** A project is promoted to
   its own second mate (a persistent firstmate with its own home and clones of
   one project) when it needs its own context or harness. The gateway keeps a
   routing table that may name a second mate for a project, so promotion changes
   the routing, not the pages. Hive is promoted after the ticket-sync pieces
   below exist, not before.
4. **A website over Tailscale is the interface.** A gateway bound to loopback
   only, published with `tailscale serve` (tailnet only, never Funnel), hosted on
   the PC beside firstmate for iPad and phone. It trusts only the owner's
   Tailscale login, replacing the shared `HIVE_WEB_TOKEN` as the primary
   credential, and adds Host/Origin checks, step-up for merge and discard, and an
   audit note per action. Hive keeps its PWA shell, Web Push channel
   ([ADR 0026](0026-web-push-notification-channel.md)) and Desk design.
5. **The gateway has three verbs and no logic of its own.** *Read* (fleet snapshot,
   status tails, reports), *say* (a note to a first mate), *decide* (answer a held
   decision). It calls firstmate's scripts, never writes project files, and pins
   the snapshot schema (`fm-fleet-snapshot.v1`), falling back to read-only on an
   unknown major version.
6. **Tickets have one owning home and are editable from both sides.** There is no
   two-way mirror. Ownership moves by handoff; the other side reads by rollup;
   an edit to a ticket the other side owns crosses as a routed request that must
   be reliable and answered, not a best-effort note. The website, the terminal
   first mate and Hive's second mate can all edit. An answer in either place
   closes the same decision. Required before Hive is promoted: owner-aware
   answers for decisions held in a second mate's home, reverse and lateral
   handoff, a one-step new-ticket, a complete rollup with explicit owner, and a
   two-home contract test in Hive CI.
7. **Harness policy.** Maestro-class (project-owning) supervisors stay Claude
   first, with Pi as the fallback when Claude cannot be reached. This is
   firstmate's harness dispatch; Hive no longer owns an adapter layer.
8. **Production.** Run it for real on the PC as a systemd user unit, trial on a
   separate `tailscale serve` port first, then the final origin, chosen once
   because changing it orphans installed PWAs and push subscriptions.

### Build order

1. Land the harness pivot (ADR 0029) as a bridge. Done.
2. This ADR and the glossary update.
3. Gateway skeleton: owner-login auth, read-only Home and Project pages from the
   fleet snapshot, trial port, snapshot-schema pin, recorded-snapshot contract
   test.
4. Act: decisions lane, chat via the inbox, control verbs, audit notes, merge
   step-up.
5. Live: SSE from status files, Web Push, live tail, final origin.
6. Land or close the parked Hive tickets so the Hive backlog can be handed off.
7. Firstmate-repo changes for the sync pieces in decision 6.
8. Two-home contract test in Hive CI.
9. Promote Hive to its own project first mate.
10. Cut over: Telegram becomes an optional ping, the Maestro runtime and entity
    tables are retired, T017 and T005 land.

## Superseded ADRs

Each body was read; an ADR is superseded only if its decision is about running
Hive's own Entities, which this ADR retires.

- [0001](0001-harness-agnostic-runtime.md) harness-agnostic runtime: firstmate drives harnesses now.
- [0004](0004-interactive-gate-hold-and-inject.md), [0005](0005-permission-gate-not-transcript-detectable.md): PTY gate bridge and its limits; the PTY adapter retires.
- [0007](0007-pty-only-runtime.md): the PTY-only runtime.
- [0008](0008-per-role-skill-curation-denylist.md), [0009](0009-adopt-native-advisor.md): per-role Entity configuration; roles retire.
- [0010](0010-leads-orchestrate-via-workflow.md), [0014](0014-workflow-progress-from-on-disk-run-record.md): Leads and Workflow-run progress; Leads retire (crews do leaf work).
- [0011](0011-session-pinning-over-directory-heuristics.md), [0012](0012-turn-end-sentinel-acceptance.md): transcript reading for Hive-run sessions.
- [0015](0015-auto-bounce-jammed-sessions.md), [0016](0016-worktree-reconciliation-scope-and-orphan-policy.md): Hive's own supervision and worktree manager; firstmate's watcher and worktree-per-task replace them.
- [0017](0017-ownership-guard-pretooluse-hook.md): the ownership guard; replaced by worktree-per-task isolation.
- [0018](0018-conversational-decision-channel.md), [0019](0019-maestro-phase-confirmation-gate.md), [0024](0024-decision-channel-entity-keyed.md): Maestro decision channel and phase gate; captain-held decisions replace them.
- [0020](0020-interaction-patterns-as-jd-recipes.md), [0021](0021-further-patterns-as-global-skills.md), [0025](0025-lead-pattern-library-awareness-pointer.md): Lead interaction-pattern delivery; Leads retire.

Not superseded, and why:

- [0006](0006-god-object-breakup-composition.md): a general code-structure rule that still applies to the code that remains.
- [0013](0013-retire-worker-creation-all-paths.md): its ban on Workers still holds, now trivially.
- [0029](0029-harness-pivot-headless-default-pty-fallback.md): the bridge; in force until the cut-over, then retired with the runtime.
- 0002, 0003, 0022, 0023, 0026, 0027, 0028: unrelated to the Entity runtime or still valid.

## ADR 0029 number

`main` carries one ADR 0029, the harness pivot. The isolation ADR on the
`fm/hive-t008` branch also used 0029; it is being folded and archived by a separate
ticket (its two ideas, worktree per task and launch-env scoping, exist in
firstmate), so it needs no number. If it is ever revived, it takes the next free
number, not 0029 or 0030.

## Consequences

- Hive stops being an orchestrator. Glossary churn: Entity, Maestro, Team Lead,
  Team, Leaf agent and Workflow run retire at cut-over; first mate, second mate,
  lens, ticket home and handoff enter.
- About 11.5k of Hive's 16.5k source lines (process, bus, runtime, commands,
  models, hooks) are retired at cut-over [UNSURE: package line counts from the
  report, not dead-code analysis].
- The supervision host is the PC; the site and pushes are down when it sleeps
  [UNSURE: sleep behaviour untested]. A remote second mate on the VPS is the later
  path for always-on projects.
- Chat with the first mate is asynchronous (inbox and receipts), so the site
  shows delivered, seen and answered states and "offline, notes queued".
- A web button is authority, hence step-up and the owner's words recorded.
- Two codebases, one contract: Python gateway over firstmate script output,
  guarded by the pinned schema and contract tests.
- Firstmate-side changes ship as PRs to the firstmate repo.
