# Hive — Project Guidelines

Project-specific rules for any Claude Code session working on Hive.

## Live context (auto-loaded)

@CONTEXT.md
@docs/roadmap.md

---

## Working method

Work tracking lives outside this repo: there is no ticket or epic board
here. GitHub issues mirror the open work. Direction is in
`docs/roadmap.md` (milestones in order, never dates) and process/tooling
decisions in `docs/decisions.md` (newest first).

### Reference docs

- **`CONTEXT.md` (glossary)** — free edits, anytime.
- **`README.md` / `docs/DEPLOYMENT.md`** (system maps + runbooks) —
  update them in the same change that alters the underlying code.
- **`docs/adr/*.md`** (architecture decisions) — append-only. New
  decision = new file with the next number. Never edit an existing
  ADR. Smaller process decisions go in `docs/decisions.md`.
- **`docs/CHANGELOG.md`** — one line per shipped milestone.
- **`docs/archive/`** — retired docs; do not edit.

### Check command

```
uv run ruff check src/ tests/ && uv run ruff format --check src/ tests/ && uv run pytest -m "not integration"
```


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
[ADR 0027](docs/adr/0027-web-delegators-desk.md)). The design work is HTML mockups authored here and reviewed in
**Lavish Editor** (the `lavish` skill; Tailscale link only, never
`lavish-axi share`), approved copies in `docs/design/`, then implemented
into `src/hive/web`. Open work is tracked outside the repo.

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
runtime), [ADR 0029](docs/adr/0029-harness-pivot-headless-default-pty-fallback.md)
(Pi first, headless default, PTY fallback — add new Harnesses as a
`HarnessSpec` in `runtime/registry.py`) and
[ADR 0006](docs/adr/0006-god-object-breakup-composition.md)
(composition pattern for the breakup) first.

## Maintaining this file

Keep this file for knowledge useful to almost every future agent session in this project.
Do not repeat what the codebase already shows; point to the authoritative file or command instead.
Prefer rewriting or pruning existing entries over appending new ones.
When updating this file, preserve this bar for all agents and keep entries concise.
