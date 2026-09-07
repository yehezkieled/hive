# Hive — Project Guidelines

Project-specific rules for any Claude Code session working on Hive.

## Live context (auto-loaded)

@CONTEXT.md
@docs/pm/roadmap.md

---

## Working method — the pm layout

Planning lives in `docs/pm/` and is driven by the **pm plugin**
([ADR 0028](docs/adr/0028-adopt-pm-plugin-layout.md)). Read this
section before doing any non-trivial work.

```
milestone -> epic -> ticket -> subtask
docs/pm/roadmap.md            milestones in order (M1, M2, ...) + Backlog
docs/pm/epics/Exx-slug.md     goal, ticket list, dependency Flow
docs/pm/tickets/Txxx-slug.md  What / Why / Acceptance / Subtasks / Plan
docs/pm/decisions.md          process + tooling decisions (newest first)
CONTEXT.md  ## pm             machine-read config: flow, gates, check
docs/adr/                     architecture decisions, append-only
docs/archive/                 retired docs (old roadmap, sprints, tickets 001–067)
```

Milestones are **ordered versions, not dates**. Never write a
deadline or a target date anywhere in `docs/pm`.

### The skills

| Want to…                                | Use            |
|-----------------------------------------|----------------|
| see the board, what's next, what's blocked | `/pm:status` |
| add a ticket, epic, bug, or idea        | `/pm:plan`     |
| make a ticket ready to build            | `/pm:grill Txxx` |
| build one ticket end to end             | `/pm:work Txxx` |
| find bugs and file them as tickets      | `/pm:audit`    |
| close a milestone + retro               | `/pm:retro`    |
| cheat sheet                             | `/pm:help`     |

Scripts: `python3 /home/hezki/projects/pm-plugin/scripts/pm.py
{board,next,flow,claim,validate,sync,...}`.

### Ticket lifecycle

1. **Created thin** by `/pm:plan` (or migrated): `ready: no`.
2. **Grilled** by `/pm:grill`: What, Why, a testable Acceptance line
   per item, `plan: none|required`, priority, `depends_on` settled →
   `ready: yes`. `/pm:work` refuses an un-grilled ticket.
3. **Built** by `/pm:work`: claim, branch, plan (approved first when
   `plan: required`), tests first, build, run the check command, two
   read-only reviews (verifier + reviewer), docs, PR.
4. **Done**: status set, epic Flow redrawn (`pm.py flow --all`),
   GitHub issue closed by `pm.py sync`.

Gates for every ticket: `tdd, checks, review, docs`. The check
command is in the `## pm` block of `CONTEXT.md`.

### Reference docs — different rules per category

Reference docs are orthogonal to the pm layout. Each category has its
own edit rule.

- **`CONTEXT.md` (glossary)** — free edits, anytime, no ticket needed.
  The `## pm` block at the bottom is machine-read: plain `key: value`
  lines only.
- **`README.md` / `docs/DEPLOYMENT.md`** (system maps + runbooks) —
  edited inside the ticket that changed the underlying code. Declare
  the impact in the ticket's Plan section.
- **`docs/adr/*.md`** (architecture decisions) — append-only. New
  decision = new file with the next number. Never edit an existing
  ADR. Smaller process decisions go in `docs/pm/decisions.md`.
- **`docs/CHANGELOG.md`** — one line per shipped milestone, appended
  at `/pm:retro`.

### Operating rules

- Every non-trivial change belongs to a ticket. Check `/pm:status`
  first; if none fits, ask whether to create one with `/pm:plan`.
- Commit the pm docs alongside the code. Tickets *are* the engineering
  record, not throwaway scratch.
- Non-goals matter: a ticket's Notes say what it is *not* building.
- Match editing energy to stability. Don't agonise over a ticket's
  wording; do agonise over the roadmap and ADRs.
- Ask before changing structure (a new artifact type, a new
  top-level folder).

---

## Environment

This Claude Code session runs directly on the VPS
(`ubuntu-s-4vcpu-8gb-sgp1-01`). There is no separate remote machine
to deploy to — everything runs here.

- **VPS**: DigitalOcean droplet, Tailscale hostname
  `tailfb3900.ts.net`, SSH on port 7777
- **Tailscale IP**: 100.79.194.84
- **n8n**: running in Docker
- **OpenClaw**: service stopped and disabled
- **Hive service**: systemd user service (`hive.service`)

Avoid "local" language — say "not pushed to origin yet" or "on this
host" instead.

## Deployment

After merging a Ticket to `main`, run autonomously without asking:

1. `git push`
2. `systemctl --user restart hive.service`
3. Verify with `journalctl --user -u hive.service -n 20`

Smoke-test from the Tailscale IP (`http://100.79.194.84:<port>/`),
not just loopback. Loopback bypasses bind address, firewall, and
routing — all of which can fail silently.

JS-rendered features (React, htmx, Babel-in-browser) require an
actual browser check. A `curl` returning 200 with the right HTML
markers is not sufficient — the browser still has to download,
compile, and mount.

## Code quality

Before every `git push`:

```
uv run ruff check src/ tests/ && uv run ruff format --check src/ tests/
```

Hive CI runs both as separate gates. Fixing lint does not fix
format — they fail independently.

## Bots

Lona and Wonder run on isolated per-bot state dirs. Always use the
`lona` and `wonder` wrapper scripts — never raw `claude --channels`.

- Lona state: `~/.claude/channels/telegram-lona/`
- Wonder state: `~/.claude/channels/telegram-wonder/`

## Active work

**M1 — the Delegator's Desk** is current (the archived roadmap's Phase 5,
[ADR 0027](docs/adr/0027-web-delegators-desk.md)). The three design
tickets (T001–T003) are produced in the external **Claude design
app** — their deliverable is an approved mockup, closed by hand — and
T004 implements them into `src/hive/web`. See `/pm:status` for the
live board.

**History.** Four milestones shipped between 2026-06-01 and 2026-06-30
(the archived roadmap calls them Phases 1–4): the PTY harness runs
plan-billed (runtime migration), `process/manager.py` is a facade +
collaborators and the headless runtime is gone (restructure,
[ADR 0006](docs/adr/0006-god-object-breakup-composition.md),
[ADR 0007](docs/adr/0007-pty-only-runtime.md)), Leads orchestrate
leaf work through the Claude Code Workflow tool and the persistent
Worker entity is retired (Workflow-native orchestration,
[ADR 0010](docs/adr/0010-leads-orchestrate-via-workflow.md),
[ADR 0013](docs/adr/0013-retire-worker-creation-all-paths.md)), and
the web is an installable PWA with Web Push (web dashboard to PWA,
[ADR 0023](docs/adr/0023-https-via-tailscale-serve-for-pwa.md),
[ADR 0026](docs/adr/0026-web-push-notification-channel.md)). Per-
ticket detail is in `docs/archive/tickets/` and the sprint files in
`docs/archive/sprints/`.

Before working on `runtime/` or the `process/` modules, read the
adapter code,
[ADR 0001](docs/adr/0001-harness-agnostic-runtime.md) (harness-agnostic
runtime) and
[ADR 0006](docs/adr/0006-god-object-breakup-composition.md)
(composition pattern for the breakup) first.
