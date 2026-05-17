# Hive — Project Guidelines

## Environment

This Claude Code session runs directly on the VPS (`ubuntu-s-4vcpu-8gb-sgp1-01`). There is no separate remote machine to deploy to — everything runs here.

- **VPS**: DigitalOcean droplet, Tailscale hostname `tailfb3900.ts.net`, SSH on port 7777
- **Tailscale IP**: 100.79.194.84
- **n8n**: running in Docker
- **OpenClaw**: service stopped and disabled (not running)
- **Hive service**: managed by systemd user service (`hive.service`)

Avoid "local" language — say "not pushed to origin yet" or "on this host" instead.

## Deployment

After merging a sprint branch, run autonomously without asking:
1. `git push`
2. `systemctl --user restart hive.service`
3. Verify with `journalctl --user -u hive.service -n 20`

Smoke-test from the Tailscale IP (`http://100.79.194.84:<port>/`), not just loopback. Loopback bypasses bind address, firewall, and routing — all of which can fail silently.

JS-rendered features (React, htmx, Babel-in-browser) require an actual browser check. A `curl` returning 200 with the right HTML markers is not sufficient — the browser still has to download, compile, and mount.

## Code quality

Before every `git push`:
```
ruff check src/ tests/ && ruff format --check src/ tests/
```
Hive CI runs both as separate gates. Fixing lint does not fix format — they fail independently.

## Documentation

After every sprint, update both:
1. `docs/PROJECT_PLAN.md` — sprint build record (phases, decisions, files, verification)
2. `docs/DEPLOYMENT.md` — startup logs, Telegram commands, config table, known limitations

## Bots

Lona and Wonder run on isolated per-bot state dirs. Always use the `lona` and `wonder` wrapper scripts — never raw `claude --channels`.

- Lona state: `~/.claude/channels/telegram-lona/`
- Wonder state: `~/.claude/channels/telegram-wonder/`

## Active work

**Billing migration** (deadline 2026-06-15): `claude -p` (headless) moves to API-only billing on that date. Hive must switch to interactive PTY sessions. Branch: `harness-migration`. Flag: `HIVE_USE_PTY`. Before working on `process/manager.py` or `runtime/`, check which branch you're on and read the adapter code first.

**Orchestrator spec**: LOCKED 2026-04-02 — frozen reference. Any drift from the live codebase is catalogued in `docs/AUDIT_2026-05-05.md`.
