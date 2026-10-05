# Hive

Hive is the front door of [firstmate](docs/adr/0030-firstmate-implemented-in-hive.md):
the **gateway** (`hive-gateway.service`) serves the owner's desk over Tailscale —
read the first mate's backlog, say, decide. Since the cut-over
([ADR 0033](docs/adr/0033-hive-cutover-retire-entity-runtime.md)) Hive no longer
runs Maestros or Team Leads. What remains of the old Entity runtime is the
**Vault** (the approval rail for payments; off by default, stub provider only),
reached over Telegram as an optional backup channel and the legacy web app. The
Vault runs on a Harness Hive picks per turn — Claude Code first, then Codex/Pi,
each in headless mode by default with Claude's interactive PTY session as the
fallback ([ADR 0029](docs/adr/0029-harness-pivot-headless-default-pty-fallback.md)).
Model defaults and overrides are documented in the
[deployment runbook](docs/DEPLOYMENT.md#harness-selection-adr-0029).
OpenCode remains planned.

See [`CONTEXT.md`](CONTEXT.md) for canonical terminology (Entity,
Vault, Harness, Plan-billed, …) and
[`docs/roadmap.md`](docs/roadmap.md) for direction.

## How it works

```
You (desk over Tailscale)  ──►  Gateway ──► firstmate scripts / records
You (Telegram, optional)   ──►  Hive orchestrator (Python asyncio)
                                    │
                                    ▼
                          Vault Entity on a Harness  ←→  PostgreSQL
                                    │                    (messages, tasks,
                                    ▼                     usage, vault_actions)
                          Approval via Telegram or the legacy web app
```

The gateway is self-contained and does not need the orchestrator. The
orchestrator (`python -m hive`) owns the Vault Entity's lifecycle, the
approval rail, notification fan-out (Telegram, SSE, Web Push, email digest)
and the legacy web app.

## Prerequisites

- Python 3.12+
- Docker + Docker Compose
- At least one signed-in Codex, Claude Code, or Pi CLI on the host
- A Telegram bot token (from [@BotFather](https://t.me/BotFather)) — optional;
  Telegram is a backup channel and stays off without it
- OpenAI API key (optional — for blueprint embeddings)

## Quick start

```bash
git clone <repo-url> hive
cd hive
python3.12 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"

cp .env.example .env
$EDITOR .env       # POSTGRES_*; optionally TELEGRAM_BOT_TOKEN, TELEGRAM_ALLOWED_USER_IDS

docker compose up -d postgres
python -m hive
```

On first run, Hive applies all DB migrations (migration 035 deletes any
pre-cut-over Maestro/Lead rows and the `projects` table — see
[ADR 0033](docs/adr/0033-hive-cutover-retire-entity-runtime.md); back up the
database first on an existing install). With `TELEGRAM_BOT_TOKEN` set you
should see `Telegram bridge started as a backup channel`; without it,
`Telegram backup channel is off` and Hive keeps running.

Full install + ops runbook in
[`docs/DEPLOYMENT.md`](docs/DEPLOYMENT.md).

## Telegram interface (optional backup)

Set `TELEGRAM_BOT_TOKEN` and `TELEGRAM_ALLOWED_USER_IDS` to enable it.
Telegram delivers pings and handles the typed vault/mode approvals; the desk is
the primary surface.

| Command | Purpose |
|---|---|
| `/status` | Overview of active Entities |
| `/cost [24h\|7d\|30d]` | Token usage and estimated cost |
| `/m:<name> <msg>` | Send a message to a named Entity |
| `/approve` `/deny` `/vault` | Mode-elevation and vault approvals |
| `/mode <yolo\|yotree> [entity]` | Set Claude permission mode |
| `/model <opus\|sonnet\|haiku> [entity]` | Switch Claude model |
| `/quota` | Plan-quota status (5h + 7d windows) |
| `/task add "<title>"` | Add a task to the queue |
| `/tasks` | List all tasks |
| `/help` | Full command list |

## Permission modes

| Mode | Use case |
|---|---|
| `plan` | Read-only — explore and plan, no writes |
| `edit` | Normal edits, no shell commands |
| `auto` | Full autonomy (`--dangerously-skip-permissions`) |

## Configuration

Key variables in `.env` (full table in
[`docs/DEPLOYMENT.md`](docs/DEPLOYMENT.md)):

```bash
TELEGRAM_BOT_TOKEN=<from BotFather>            # optional
TELEGRAM_ALLOWED_USER_IDS=<your numeric Telegram user id>

POSTGRES_HOST=127.0.0.1
POSTGRES_PORT=5433
POSTGRES_DB=hive
POSTGRES_USER=hive
POSTGRES_PASSWORD=hive

OPENAI_API_KEY=<optional>       # blueprint embeddings
```

## Development

```bash
# Tests (spins up a throwaway Postgres container — won't touch dev DB)
.venv/bin/python -m pytest tests/ -v

# Lint + format
.venv/bin/python -m ruff check src/ tests/
.venv/bin/python -m ruff format src/ tests/
```

## Project structure

```
src/hive/
├── __main__.py        # entry point
├── config.py
├── runtime/           # harness registry + adapters (Pi, Claude headless/PTY)
├── process/           # Vault session lifecycle, approvals, wake-on-inbound
├── models/            # Entity, Task, Vault
├── bus/               # message routing + persistence
├── telegram/          # Telegram bridge + command parser
├── commands/          # /command handlers
├── web/               # legacy FastAPI app (vault/mode approvals)
├── gateway/           # owner desk over Tailscale: read + act (ADR 0030)
├── knowledge/         # blueprints + embeddings
├── vault/             # security-gated payment Entity
├── notifications/
├── observability/     # /status, /cost, daily summary, heartbeat
└── mcp/               # MCP config + hive-knowledge server
```

## Project documentation

- [`CONTEXT.md`](CONTEXT.md) — terminology
- [`docs/roadmap.md`](docs/roadmap.md) — milestones in order + backlog
- [`docs/decisions.md`](docs/decisions.md) — process and tooling decisions
- [`docs/archive/`](docs/archive/) — retired roadmap, sprints, and tickets 001–067
- [`docs/adr/`](docs/adr/) — architecture decisions
- [`docs/DEPLOYMENT.md`](docs/DEPLOYMENT.md) — install + ops runbook
- [`docs/CHANGELOG.md`](docs/CHANGELOG.md) — what shipped, when
