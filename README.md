# Hive

Multi-maestro AI agent orchestration platform built natively on Claude Code. Hive lets you run and coordinate multiple Claude agents (maestros, teams, workers) via Telegram — each agent runs as a `claude -p` subprocess with its own conversation history, personality, and permissions.

## How it works

```
You (Telegram)
    ↓
Telegram Bridge  ←→  Command Router
    ↓
Process Manager
    ↓
claude -p subprocess  ←→  PostgreSQL (message history, tasks, usage)
    ↓
Telegram reply
```

Each entity (maestro / team lead / worker) is a persistent Claude Code session. The orchestrator spawns a subprocess per message, resuming the conversation via `--resume <session_id>`, and records token usage and messages to PostgreSQL.

## Prerequisites

- Python 3.12+
- Docker + Docker Compose (for PostgreSQL with pgvector)
- Claude Code CLI — `claude -p` must work on the host
- A Telegram bot token (from [@BotFather](https://t.me/BotFather))
- OpenAI API key (optional — required only for blueprint embeddings)

## Quick start

```bash
# 1. Clone and install
git clone <repo-url> hive
cd hive
python3.12 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"

# 2. Configure
cp .env.example .env
$EDITOR .env   # fill in TELEGRAM_BOT_TOKEN, TELEGRAM_ALLOWED_USER_IDS, POSTGRES_* vars

# 3. Start PostgreSQL
docker compose up -d postgres

# 4. Start Hive
python -m hive
```

On first run, Hive applies all DB migrations and registers a default maestro named `pa`. You should see `Telegram bridge started, polling for updates` in the logs.

See [`docs/DEPLOYMENT.md`](docs/DEPLOYMENT.md) for the full install runbook, systemd setup, troubleshooting, and all config variables.

## Telegram interface

**Talk to an entity:**
```
/m:dev what's the status of the auth refactor?
```

**Common commands:**

| Command | Purpose |
|---|---|
| `/status` | Overview of all active entities |
| `/cost [24h\|7d\|30d]` | Token usage and estimated API cost |
| `/m:<name> <msg>` | Send a message to a named entity |
| `/mode <plan\|edit\|auto\|yolo> [entity]` | Set Claude permission mode |
| `/loop <ralph\|ship-it\|plan-act-observe\|build-test-refine> [entity]` | Set agent reasoning loop |
| `/model <opus\|sonnet\|haiku> [entity]` | Switch model |
| `/task add "<title>"` | Add a task to the queue |
| `/tasks` | List all tasks |
| `/org` | Show entity hierarchy |
| `/help` | Full command list |

No `TELEGRAM_BOT_TOKEN`? Hive falls back to a local readline CLI — useful for debugging without a bot.

## Entity modes and loops

**Permission modes** control what Claude can do:

| Mode | Claude flag | Use case |
|---|---|---|
| `plan` | `--permission-mode plan` | Read-only — explore and plan, no writes |
| `edit` | `--permission-mode default` | Normal edits, no shell commands |
| `auto` | `--dangerously-skip-permissions` | Full autonomy, use carefully |

**Loops** are system prompt instructions that shape how the agent approaches a task:

| Loop | Behaviour |
|---|---|
| `ralph` | Read → Ask → List → Plan → Halt (verbose, checkpoint-heavy) |
| `ship-it` | Fast execution, minimal stopping |
| `plan-act-observe` | Iterative, data-driven |
| `build-test-refine` | Ship, test, iterate |

## Configuration

Key variables in `.env` (see [`docs/DEPLOYMENT.md § Configuration`](docs/DEPLOYMENT.md) for the full table):

```bash
TELEGRAM_BOT_TOKEN=<from BotFather>
TELEGRAM_ALLOWED_USER_IDS=<your numeric Telegram user ID>

POSTGRES_HOST=127.0.0.1
POSTGRES_PORT=5433
POSTGRES_DB=hive
POSTGRES_USER=hive
POSTGRES_PASSWORD=hive

# Optional — enables blueprint save/search and auto-retrieval
OPENAI_API_KEY=<your key>
```

## Development

```bash
# Tests (spins up a throwaway Postgres container — won't touch your dev DB)
.venv/bin/python -m pytest tests/ -v

# Lint + format
.venv/bin/python -m ruff check src/ tests/
.venv/bin/python -m ruff format src/ tests/
```

## Project structure

```
src/hive/
├── __main__.py          # entry point, wires up all components
├── config.py            # env var loading
├── telegram/
│   └── bridge.py        # Telegram message handler and command router
├── process/
│   ├── manager.py       # session lifecycle, spawns claude -p subprocesses
│   ├── claude_session.py# subprocess wrapper (stdin/stdout pipes)
│   └── loops.py         # loop mode prompt strings
├── models/
│   └── entity.py        # Entity model, builds CLI args for claude -p
├── bus/
│   └── router.py        # in-memory message routing between entities
└── storage/
    ├── message_store.py  # PostgreSQL message persistence
    └── migrations/       # SQL migration files (applied on startup)
```

## Deployment

For running Hive as a persistent systemd user service on a Linux VPS, see [`docs/DEPLOYMENT.md`](docs/DEPLOYMENT.md). It covers:
- systemd unit file setup
- Log tailing and graceful restarts
- Database verification queries
- Troubleshooting common failures
