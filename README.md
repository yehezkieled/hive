# Hive

Hive is a multi-agent orchestration platform: it runs and coordinates
a fleet of AI coding agents that you control from Telegram. Each
Entity (Maestro / Team Lead) runs on its own Harness —
Claude Code via an interactive PTY session today, with Codex and
OpenCode adapters planned.

See [`CONTEXT.md`](CONTEXT.md) for canonical terminology (Entity,
Maestro, Harness, Plan-billed, …) and
[`docs/roadmap.md`](docs/roadmap.md) for direction.

## How it works

```
You (Telegram)
    │
    ▼
Hive orchestrator (Python asyncio)
    │
    ▼
One Harness per Entity  ←→  PostgreSQL (messages, tasks, usage)
    │
    ▼
Telegram reply
```

The orchestrator owns Entity lifecycle, message routing, and Telegram
integration. Each Entity runs on the Harness it's assigned to via an
Adapter — uniform turn-level interface, harness-specific internals.

## Prerequisites

- Python 3.12+
- Docker + Docker Compose
- Claude Code CLI on the host (`claude` must work)
- A Telegram bot token (from [@BotFather](https://t.me/BotFather))
- OpenAI API key (optional — for blueprint embeddings)

## Quick start

```bash
git clone <repo-url> hive
cd hive
python3.12 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"

cp .env.example .env
$EDITOR .env       # TELEGRAM_BOT_TOKEN, TELEGRAM_ALLOWED_USER_IDS, POSTGRES_*

docker compose up -d postgres
python -m hive
```

On first run, Hive applies all DB migrations and registers a default
Maestro. You should see `Telegram bridge started, polling for updates`
in the logs.

Full install + ops runbook in
[`docs/DEPLOYMENT.md`](docs/DEPLOYMENT.md).

## Telegram interface

Send `/m:<entity> <msg>` to talk to an Entity, e.g.
`/m:dev what's the status of the auth refactor?`.

Common commands:

| Command | Purpose |
|---|---|
| `/status` | Overview of all active Entities |
| `/cost [24h\|7d\|30d]` | Token usage and estimated cost |
| `/m:<name> <msg>` | Send a message to a named Entity |
| `/mode <plan\|edit\|auto\|yolo> [entity]` | Set Claude permission mode |
| `/loop <ralph\|ship-it\|plan-act-observe\|build-test-refine> [entity]` | Set agent reasoning loop |
| `/model <opus\|sonnet\|haiku> [entity]` | Switch model |
| `/runtime <entity> <harness> [model]` | Switch the Entity's Harness |
| `/quota` | Plan-quota status (5h + 7d windows) |
| `/task add "<title>"` | Add a task to the queue |
| `/tasks` | List all tasks |
| `/org` | Show entity hierarchy |
| `/help` | Full command list |

No `TELEGRAM_BOT_TOKEN`? Hive falls back to a local readline CLI —
useful for debugging without a bot.

## Modes and loops

**Permission modes** control what Claude can do:

| Mode | Use case |
|---|---|
| `plan` | Read-only — explore and plan, no writes |
| `edit` | Normal edits, no shell commands |
| `auto` | Full autonomy (`--dangerously-skip-permissions`) |

**Loops** are system-prompt strategies for how the Entity approaches
a task:

| Loop | Behaviour |
|---|---|
| `ralph` | Read → Ask → List → Plan → Halt (verbose, checkpoint-heavy) |
| `ship-it` | Fast execution, minimal stopping |
| `plan-act-observe` | Iterative, data-driven |
| `build-test-refine` | Ship, test, iterate |

## Configuration

Key variables in `.env` (full table in
[`docs/DEPLOYMENT.md`](docs/DEPLOYMENT.md)):

```bash
TELEGRAM_BOT_TOKEN=<from BotFather>
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
├── runtime/           # harness-agnostic adapter (Claude, …)
├── process/           # Entity / session lifecycle
├── models/            # Entity, Team, Task, Vault, …
├── bus/               # message routing + persistence
├── telegram/          # Telegram bridge + command parser
├── commands/          # /command handlers
├── web/               # FastAPI dashboard
├── knowledge/         # blueprints + embeddings
├── vault/             # security-gated payment Entity
├── notifications/
├── observability/     # /status, /cost, daily summary, heartbeat
├── mcp/               # MCP config + hive-knowledge server
└── cli/               # local readline CLI fallback
```

## Project documentation

- [`CONTEXT.md`](CONTEXT.md) — terminology
- [`docs/roadmap.md`](docs/roadmap.md) — vision and themes
- [`docs/sprints/`](docs/sprints/) — current and past sprint plans
- [`docs/tickets/INDEX.md`](docs/tickets/INDEX.md) — Ticket registry
- [`docs/adr/`](docs/adr/) — architecture decisions
- [`docs/DEPLOYMENT.md`](docs/DEPLOYMENT.md) — install + ops runbook
- [`docs/CHANGELOG.md`](docs/CHANGELOG.md) — what shipped, when
