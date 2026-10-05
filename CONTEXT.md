# Hive

Hive is a multi-agent orchestration platform: it runs and coordinates a fleet
of AI coding agents that you control from Telegram.

## Language

### Entities

**Entity**:
The only thing Hive still runs as an AI agent: the **Vault**. Since the
cut-over ([ADR 0033](docs/adr/0033-hive-cutover-retire-entity-runtime.md)) Hive
runs no Maestro or Team Lead; the `entities` table, `ProcessManager` and the
adapters survive only to serve the Vault's approval rail. Day-to-day
supervision of software work is the **First mate**'s (see below).
_Avoid_: agent, bot

**Vault**:
The security-gated Entity behind payments. It cannot run Bash/Write/Edit, can
only be killed by the user, and its `request_payment` actions become
`vault_actions` rows that the user approves or denies (`/vault`, `/approve`,
`/deny` on Telegram; the legacy web app's approval buttons). Off by default
(`HIVE_VAULT_ENABLED`), stub payment provider only; no real provider is
connected.
_Avoid_: wallet, treasury

**Retired at the cut-over**:
**Maestro**, **Team Lead**, **Team**, **Worker**, **Leaf agent**, **Project**
(and **Project ownership** / **Ownership guard**) are gone, with their code and
database rows (migration 035). Their work is now done by the first mate, second
mates and firstmate's crews.


### Firstmate in Hive

Direction per [ADR 0030](docs/adr/0030-firstmate-implemented-in-hive.md): Hive is
the front door of firstmate. The cut-over
([ADR 0033](docs/adr/0033-hive-cutover-retire-entity-runtime.md)) retired
**Maestro**, **Team Lead**, **Team**, **Leaf agent** and **Workflow run**. The
Execution terms below (**Adapter**, **Turn**, **Auto-bounce**, …) remain only as
the machinery behind the **Vault**.

**First mate**:
The single supervisor across all projects: the owner's one point of contact for
software work, and the owner of the backlog. Hive's website talks to it through
the gateway; it replaced the PA Maestro.
_Avoid_: PA Maestro, orchestrator, boss.

**Second mate**:
A persistent firstmate with its own home, its own backlog and clones of one
project, created for a project only when that project earns one (its own context
or harness). Replaced the project Maestro. Hive is promoted to one after the
ticket-sync pieces exist.
_Avoid_: project Maestro, sub-agent.

**Lens**:
A project page on the website: the first mate's backlog, crews, parked work,
landed PRs and reports filtered to one project. A lens is a view, not a
supervisor; which agent answers is a routing detail, shown by the chat-target chip.
_Avoid_: workspace, tenant.

**Ticket home**:
The one backlog that owns a ticket (the first mate's, or a second mate's). There
is no two-way mirror: the home edits directly, everyone else reads by rollup and
changes it by a routed request. Tickets must be editable from the terminal
first mate, the website and a second mate, or they do not get finished.
_Avoid_: sync, mirror, source of truth (one home per ticket, not one global copy).

**Handoff**:
Moving a ticket, with its dependency-closed set, from one home to another,
atomically and idempotently. Today only main to second mate and only queued
items; reverse and lateral handoff are to be built.
_Avoid_: reassignment, copy.

**Gateway**:
The loopback-only service behind the website, published with `tailscale serve`
(tailnet only), trusting the owner's Tailscale login. Three verbs — read, say,
decide — each calling firstmate's scripts; it holds no work logic and never
writes project files.
_Avoid_: backend, API server.

### Execution

**Harness**:
A standalone agentic CLI that runs a full agent loop — reasoning, tool use,
file editing — on its own. Hive drives one Harness per Entity. Supported today:
Codex, Claude Code, and Pi; OpenCode is planned. A Harness is not a bare model;
it is the whole agent tool wrapped around one.
_Avoid_: runtime, model, LLM, backend

**Adapter**:
The Hive code that drives one Harness in one **Run mode** and presents the rest
of Hive a uniform, turn-level interface. Registered per Harness as a
`HarnessSpec` (`runtime/registry.py`) — the extension point for new Harnesses.

**Run mode**:
How a Harness is driven for a Turn — **headless** (one non-interactive
subprocess per Turn: `codex exec`, `claude -p`, `pi -p`; the default) or **PTY** (a
persistent interactive session; the fallback, entered only when headless is
refused or out of quota, read from the Harness's own error). Chosen per Turn by
`HarnessRuntime`, which also picks the Harness (a per-role order: Codex first by
default, Claude Code first for the Vault) from whichever are
installed and signed in. Surfaced as "harness (mode)" on `/status`
and in Telegram. ADR 0029.
_Avoid_: runtime (a Runtime is which Harness an Entity is on), transport.

**Runtime**:
The Harness a given Entity is currently assigned to run on. "Switch the Vault's
runtime" means "move it to a different Harness."

**Turn**:
One prompt sent to an Entity and the full response that comes back.

**Turn-end sentinel**:
The record a Harness itself writes into its transcript when a Turn truly
completes — on Claude Code, the `turn_duration` system entry. Written by
the Harness binary, so it is deterministic: the model cannot forget,
fake, or race it, unlike anything the model emits. Hive accepts a Turn
on the sentinel; quiescence guessing is fallback only.
_Avoid_: done-marker, end-of-turn message

**Session pinning**:
Binding an Entity's Adapter to the exact Harness session it spawned —
identified by the Harness's own session record — instead of inferring
which transcript is the Entity's from directory activity. Eliminates
silent cross-Entity transcript mix-ups when sessions share a directory.
_Avoid_: transcript guessing, session sniffing

**hive_actions**:
The protocol an Entity uses to act on the rest of Hive — message a peer,
request a spawn, finish a task. The Entity emits a `<hive_actions>` block;
Hive parses it and routes the actions.

**Interactive gate**:
A point mid-Turn where the Harness pauses for human input rather than
completing the Turn — plan-mode approval (`ExitPlanMode`), an
`AskUserQuestion` call, or a permission prompt. On the PTY Harness a gate
blocks the Turn until answered and Hive bridges it (hold-and-inject).
_Avoid_: prompt, menu, interrupt

**Auto-bounce**:
Hive automatically killing a jammed Entity's Harness session and respawning
it — the conversation preserved via the Harness's own resume — when the
Entity stalls past a threshold and is **not** legitimately waiting (at an
**[[Interactive gate]]**). The recovery
net for jams a transcript can't reveal: an un-bridgeable permission prompt
(ADR 0005) or a wedged session. Repeated bounces in a short window stop and
escalate to the user instead of flapping. Per Ticket 020 / ADR 0015.
_Avoid_: restart, reboot, kill-and-retry.

**Advisor**:
Claude Code's native `/advisor` tool — a stronger model (Opus) the
executor consults at decision points for a second opinion. Enabled
per-Entity by the role file's `**Advisor**:` field (`--advisor <model>`
at spawn); model-driven and Plan-billed. _Note_: from Ticket 013 this is
the **native** tool. The retired *custom advisor* (a Hive MCP server that
spawned a `claude -p` subprocess) is gone — do not conflate them.
_Avoid_: custom advisor, advisor MCP server, `claude -p` advisor.

**Notification channel**:
One outbound delivery target for Hive's notifications. All channels implement a
single async `send(Notification)` protocol and register with the
**NotificationDispatcher**, which fans every event out to all of them with
per-channel error isolation. The live channels are Telegram, SSE (the dashboard
live stream), Email digest, and — from Ticket 041 — **[[Web Push channel]]**.
The one emit point is `ProcessManager._notify(text, kind, data)`.
_Avoid_: notifier, sink, bridge (Telegram's class is a `Bridge`, but the
abstraction is a channel).

**Web Push channel**:
The **[[Notification channel]]** (Ticket 041) that delivers native push
notifications to an installed PWA — the iPad's async-ping tier. It filters the
dispatcher's events to the **actionable set** (`ALERT_KINDS` in
`notifications/dispatcher.py`: `mode_request`, `vault_action_pending`), signs each with VAPID,
and POSTs to every stored **[[Push subscription]]**, pruning any the push
service reports `410 Gone`. Inert until VAPID keys are configured. Requires an
installed PWA on iOS/iPadOS 16.4+ over HTTPS (ADR 0023).
_Avoid_: push notifier, APNs (Apple's transport sits underneath; Hive speaks
the Web Push standard, not APNs directly).

**Push subscription**:
A browser's durable handle for receiving Web Push — an `endpoint` URL plus the
`p256dh`/`auth` crypto keys — created by the service worker's `pushManager` and
POSTed to `/api/push-subscribe`. Stored per `endpoint` (unique per
device+browser; Hive has no per-user identity — `HIVE_WEB_TOKEN` is a single
shared secret). Pruned on `410 Gone`.
_Avoid_: device token, registration.

**Alert role**:
The notification tier that pings you when you are **away** from the dashboard —
distinct from the **log/debug role** (a passive record you read when you choose
to). Until Ticket 041, Telegram carried both. 041 moves the alert role to the
**[[Web Push channel]]** and adds a `HIVE_TELEGRAM_ALERTS` toggle (default on)
that, when turned off, silences Telegram's alert-kinds while it stays a
debug/log surface — Telegram is demoted, never deleted.
_Avoid_: alerting, push (the role, not the transport).

### Billing

**Plan-billed**:
A Turn whose cost is covered by a flat-rate subscription — Claude Code on a
Claude Max plan, Codex on a ChatGPT/Codex plan. Capped by the plan's quota
windows, not charged per token.
_Avoid_: subsidised

**API-billed**:
A Turn metered per-token against an API key, paid in real money. Hive treats
API-billed usage as the expensive path, used only by deliberate choice.
_Avoid_: pay-as-you-go, raw API

**Plan quota**:
The usage allowance on a Plan-billed Harness, expressed as utilization
(0–100%) of a rolling window. Two windows run at once — a 5-hour window
and a 7-day window. Plan quota is account-wide: the developer's own
Claude usage draws it down alongside Hive's Entities. It is not the same
as per-Turn token counts. When a window reaches 100%, Turns on that
Harness fail until it resets.
_Avoid_: rate limit, token usage

### Project management

Direction lives in `docs/roadmap.md`; work tracking lives outside the repo
(GitHub issues mirror open work). The older layouts are archived under
`docs/archive/`.

**Milestone**:
An ordered version of Hive — what the product does for its user when
the milestone is done. Listed top to bottom in `docs/roadmap.md`;
never a calendar target. Ideas with no milestone yet sit in the
roadmap's Backlog.
_Avoid_: sprint, release date. "Phase" is only the archived roadmap's
label for its milestones (Phases 1–8); do not use it for new work.

**Epic**:
A big piece of work inside a Milestone — a goal plus the Tickets that
reach it. Tracked outside the repo.
_Avoid_: theme, track, sprint.

**Ticket**:
One unit of work, tracked outside the repo and mirrored as a GitHub issue.
_Note_: Tickets `001`–`067` in `docs/archive/tickets/` are the legacy
folder-per-ticket form; the open ones were later re-tracked as `T0xx` tickets, now outside the repo.
_Avoid_: task (overloaded — `/task add` in Telegram is a different
concept), feature (a roadmap-level idea that may eventually become
one or more Tickets), issue (the GitHub mirror of a Ticket, not the
Ticket itself).

**Sprint** _(retired)_:
The former 2-week calendar window holding committed Tickets
(`docs/archive/sprints/`). Sprints 0–31 in `docs/archive/PROJECT_PLAN.md`
and `docs/CHANGELOG.md` are older still — single units of shipped work.
Replaced by ordered **Milestones**.

## Relationships

- The **Vault** is the only **Entity**; it runs on one **Harness** at a time,
  through that Harness's **Adapter**
- The **First mate** supervises all software work through the **Gateway**;
  **Second mates** own projects that earn one
- A **Harness** is either **Plan-billed** or **API-billed**, depending on how
  it authenticates
- **Telegram** is an optional backup **Notification channel** (and typed
  approval surface); it is off unless `TELEGRAM_BOT_TOKEN` is set

## Example dialogue

> **Dev:** "If the Vault is running on the Codex Harness, is it still an Entity Hive manages?"
> **Hezki:** "Yes. The Harness only executes its Turns — Hive still owns the Vault's lifecycle and its approval rail. Swap the Harness and it's the same Entity."
> **Dev:** "So 'runtime' is just the Harness it's on right now?"
> **Hezki:** "Right. And the Adapter for that Harness is the code that actually drives it."

## Flagged ambiguities

- **"agent" vs "Entity"** — the README and older docs say "agent"; the code's base class is `Entity`. Resolved: **Entity** is canonical.
- **"runtime" vs "Harness"** — a **Harness** is the external tool; a **Runtime** is which Harness an Entity is assigned to. Not synonyms.
- **"subsidised"** — informal word for **Plan-billed**. Use Plan-billed.
- **"project"** — the **Project** ownership record retired with the Maestro
  (ADR 0033). "Project" now means a codebase the first mate or a second mate
  supervises, or, in `docs/`, the project-management sense (Milestones/Epics/Tickets).
