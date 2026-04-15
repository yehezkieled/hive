# Hive — Deployment Runbook

Commands and procedures for installing, running, and maintaining the Hive
orchestrator on a Linux VPS. This is a literal record of what works, not a
polished ops doc — re-run these steps in order and you should get a working
installation.

Every path is absolute to the canonical install location
(`/home/hezki/projects/hive`). Adjust for other hosts.

---

## 1. Prerequisites

### System packages

```bash
sudo apt install python3.12-venv python3-pip gh
```

Docker + Docker Compose plugin must already be installed (we use
`postgres:16-alpine` in a container — see Section 3 for why). On this VPS
they're already present because n8n runs in Docker.

### Python tooling

`uv` is the preferred dep manager, but `pip` in a venv works too. All the
commands below use a venv at `.venv/`.

### Authentication

- **Claude Code CLI** — `claude -p` must work on this host. The orchestrator
  spawns it as a subprocess for every maestro.
- **Telegram bot token** — create a bot via BotFather, paste the token into
  `.env` (see Section 2).
- **GitHub** (optional, for pushing) — `gh auth login` + `git config --global
  user.name/user.email`.

---

## 2. First-time setup

### Clone + install

```bash
cd ~/projects
git clone <repo-url> hive   # or already checked out
cd hive
python3.12 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

The dev extras pull in `pytest`, `testcontainers[postgres]`, and `ruff`.

### Create `.env`

Copy the template and fill in real values:

```bash
cp .env.example .env
$EDITOR .env
```

Required variables:

```bash
# Telegram
TELEGRAM_BOT_TOKEN=<from BotFather>
TELEGRAM_ALLOWED_USER_IDS=<your numeric Telegram user id>

# PostgreSQL (defaults match docker-compose.yml)
POSTGRES_HOST=127.0.0.1
POSTGRES_PORT=5433
POSTGRES_DB=hive
POSTGRES_USER=hive
POSTGRES_PASSWORD=hive
```

`.env` is gitignored. Do not commit it.

---

## 3. Start PostgreSQL

Hive uses PostgreSQL via `asyncpg`. The repo ships a `docker-compose.yml`
that runs `postgres:16-alpine` on `127.0.0.1:5433` with a named volume
`hive_pgdata`:

```bash
docker compose up -d postgres
docker compose ps
```

Expected output: `hive-postgres` container is `Up (healthy)` with
`127.0.0.1:5433->5432/tcp`. The healthcheck uses `pg_isready`; it takes
~5–10s to go green on first start.

**Why Docker instead of `apt install postgresql`**: one `docker compose up`
command vs learning apt/systemd/pg_hba.conf/firewall config. The binding is
`127.0.0.1:5433` (not `0.0.0.0`) so the DB is not reachable from the
internet. Named volume `hive_pgdata` survives container restarts. Migration
path to managed PG (RDS, Supabase, Neon) later is a DSN swap.

### Verify connectivity

```bash
docker exec hive-postgres psql -U hive -d hive -c '\dt'
```

Initially the table list is empty — that's expected. Migrations run on the
first hive startup (Section 4).

---

## 4. Start the orchestrator

First run:

```bash
cd /home/hezki/projects/hive
source .venv/bin/activate
nohup python -m hive > data/hive.log 2>&1 &
echo "PID: $!"                      # shell wrapper PID (may die after launch)
sleep 3 && tail -25 data/hive.log   # watch startup
```

Expected log lines on a fresh install:

```
Starting Hive orchestrator...
Running migration 001_messages.sql
Running migration 002_entities.sql
Running migration 003_token_usage.sql
Running migration 004_tasks.sql
Running migration 005_audit_log.sql
Registered entity: dev
Registered default maestro: dev
Telegram bridge started, polling for updates
Running with Telegram bridge
```

Migrations are idempotent and tracked in `schema_migrations` — subsequent
startups skip already-applied ones silently.

### Find the actual python PID

`$!` from `nohup … &` points at the shell wrapper, which often dies right
after launch (reparenting the python process to init). Use `pgrep`:

```bash
pgrep -af 'python -m hive'
```

The row with `python -m hive` as the exact command (not a wrapper bash
line) is the one to kill later.

---

## 5. Normal operations

### Tail logs

```bash
tail -f /home/hezki/projects/hive/data/hive.log
```

The log is append-only; rotate or truncate manually if it grows too large.

### Graceful shutdown

```bash
kill $(pgrep -f 'python -m hive' | tail -1)
```

Send **SIGTERM** (`kill`, not `kill -9`). The orchestrator installs a
signal handler that runs `bridge.stop()`, `process_manager.kill_all()`,
and `store.close()` so the asyncpg pool closes cleanly. SIGKILL strands
connections.

Expected shutdown log tail:

```
Shutting down...
Application.stop() complete
Telegram bridge stopped
Killed entity: dev
Hive stopped.
```

### Restart

```bash
kill $(pgrep -f 'python -m hive' | tail -1)
sleep 2 && pgrep -af 'python -m hive' | grep -v pgrep    # expect empty
source .venv/bin/activate
nohup python -m hive > data/hive.log 2>&1 &
sleep 3 && grep -E 'Restored|Registered default|Running migration' data/hive.log
```

After Sprint 2a, entities survive restart. Subsequent startup logs should
show `Restored persisted entity: <name>` for each persisted entity and
**skip** the `Registered default maestro` line (the first-run branch
short-circuits when `dev` is already restored).

### Telegram commands added in 2b

- **`/cost [24h|7d|30d]`** — token totals + API-equivalent cost over a
  window. Default window is 24h. Cost is labeled as *equivalent API
  cost (covered by Max subscription)* — it is not money actually spent.
- **`/task add "<title>"`** — create a pending task. Quotes optional.
- **`/task done <id>`** — mark a task completed (sets `completed_at`).
- **`/task cancel <id>`** — mark a task cancelled (no `completed_at`).
- **`/tasks`** — list open tasks (pending + in-progress), priority
  first, then oldest-first within a priority.
- **`/audit [entity|command|task]`** — last 20 audit events, newest
  first. Bare `/audit` shows everything; the optional category filters
  on the action prefix.

---

## 6. Verification commands

Schema + migration state:

```bash
docker exec hive-postgres psql -U hive -d hive -c '\dt'
docker exec hive-postgres psql -U hive -d hive -c \
  'SELECT version, applied_at FROM schema_migrations ORDER BY version'
```

Entity roster:

```bash
docker exec hive-postgres psql -U hive -d hive -c \
  'SELECT name, role, state, model, pid, updated_at FROM entities'
```

Recent messages:

```bash
docker exec hive-postgres psql -U hive -d hive -c \
  "SELECT id, sender, recipient, substring(content, 1, 50), status, timestamp
   FROM messages ORDER BY id DESC LIMIT 10"
```

Table column detail:

```bash
docker exec hive-postgres psql -U hive -d hive -c '\d+ entities'
docker exec hive-postgres psql -U hive -d hive -c '\d+ messages'
docker exec hive-postgres psql -U hive -d hive -c '\d+ token_usage'
docker exec hive-postgres psql -U hive -d hive -c '\d+ tasks'
docker exec hive-postgres psql -U hive -d hive -c '\d+ audit_log'
```

Token usage totals (matches `/cost`):

```bash
docker exec hive-postgres psql -U hive -d hive -c \
  "SELECT entity_name, sum(input_tokens) AS in_tok,
          sum(output_tokens) AS out_tok, sum(cost_usd) AS cost
   FROM token_usage
   WHERE recorded_at > NOW() - INTERVAL '24 hours'
   GROUP BY entity_name"
```

Task queue state:

```bash
docker exec hive-postgres psql -U hive -d hive -c \
  "SELECT id, title, status, priority, assigned_to, created_by
   FROM tasks ORDER BY status, priority, created_at"
```

Audit event distribution (useful sanity check — should show a healthy
mix of `command.*`, `entity.*`, and `task.*`):

```bash
docker exec hive-postgres psql -U hive -d hive -c \
  "SELECT action, count(*) FROM audit_log GROUP BY action ORDER BY action"
```

Recent audit trail:

```bash
docker exec hive-postgres psql -U hive -d hive -c \
  "SELECT timestamp, actor, action, target FROM audit_log
   ORDER BY timestamp DESC LIMIT 20"
```

---

## 7. Running the test suite

Tests use a session-scoped `testcontainers` PostgreSQL container, separate
from the live docker-compose one — they don't touch the dev DB:

```bash
.venv/bin/python -m pytest tests/ -v
```

Initial run pulls the `postgres:16-alpine` image (~80 MB). Subsequent runs
reuse the cached image; a full suite takes ~14s.

Style:

```bash
.venv/bin/python -m ruff check src/ tests/
.venv/bin/python -m ruff format src/ tests/
```

---

## 8. Troubleshooting

### Orchestrator won't start

Tail `data/hive.log`, read the actual error. Common culprits in order of
frequency:

1. **PG container stopped** — `docker compose up -d postgres` and wait for
   healthy.
2. **`.env` missing POSTGRES_* vars** — `config.py` has sensible defaults
   (`127.0.0.1:5433/hive` as user `hive`) but if you set a partial override
   (e.g. only `POSTGRES_HOST`), the DSN can get malformed. Check
   `src/hive/config.py:23-31`.
3. **Stale venv without asyncpg** — `pip install -e ".[dev]"` again.
4. **Port 5433 already in use** — another service grabbed it. Either stop
   that service or change `POSTGRES_PORT` in `.env` and the published port
   in `docker-compose.yml`.

### Migration failed mid-run

Inspect `schema_migrations` and the partially-created table:

```bash
docker exec hive-postgres psql -U hive -d hive -c 'SELECT * FROM schema_migrations'
```

Clean a bad version manually:

```bash
docker exec hive-postgres psql -U hive -d hive -c \
  'DELETE FROM schema_migrations WHERE version = N; DROP TABLE IF EXISTS <table>'
```

Then restart hive — the migration runner will re-apply.

### Telegram bridge can't poll (409 Conflict)

Only one process can poll a given bot. Symptoms: log shows
`telegram.error.Conflict: terminated by other getUpdates request`. Kill any
other process holding the same token:

```bash
pgrep -af telegram
pgrep -af openclaw
```

On this VPS, the OpenClaw systemd service used to conflict — it's now
stopped and disabled (`sudo systemctl stop openclaw && sudo systemctl
disable openclaw`).

### Fresh-start the database

**Destructive — wipes all hive data:**

```bash
docker compose down
docker volume rm hive_pgdata
docker compose up -d postgres
# next `python -m hive` will re-run all migrations
```

Insurance: the pre-port SQLite DB is backed up at
`/home/hezki/projects/hive/data/hive.db.sqlite-bak` (22 messages, no
entities — Sprint 0+1 state). There's no SQLite → PG migration script;
treat the backup as reference-only.

### Roll back a bad commit

```bash
git log --oneline -10              # find the last known-good commit
git reset --soft HEAD~N            # drops N commits, keeps working tree
```

Never `git reset --hard` unless you've verified there's nothing you want
in the working tree.

---

## 9. Configuration reference

All env vars are read in `src/hive/config.py`. Defaults in parentheses.

| Variable | Purpose |
|---|---|
| `TELEGRAM_BOT_TOKEN` | Bot API token (from BotFather) — no default, required for Telegram mode |
| `TELEGRAM_ALLOWED_USER_IDS` | Comma-separated numeric Telegram user IDs allowed to talk to hive |
| `POSTGRES_HOST` (`127.0.0.1`) | PG host |
| `POSTGRES_PORT` (`5433`) | PG port — matches `docker-compose.yml`'s published port |
| `POSTGRES_DB` (`hive`) | DB name |
| `POSTGRES_USER` (`hive`) | User |
| `POSTGRES_PASSWORD` (`hive`) | Password |
| `DEFAULT_MAESTRO` (`dev`) | Auto-registered maestro name on first run |
| `DEFAULT_MODEL` (`sonnet`) | Model for the default maestro |
| `MAX_CONCURRENT_SESSIONS` (`3`) | Process manager concurrency cap |

If `TELEGRAM_BOT_TOKEN` is empty/unset, hive drops to a local readline
CLI instead of starting the Telegram bridge — useful for debugging.

---

## 10. Known limitations (as of Sprint 2b)

- **No systemd service** — still launched via `nohup`. Survive-reboot is
  pending.
- **No persistent sessions** — every Telegram message spawns a fresh
  `claude -p` subprocess and kills it when done (one-shot mode). The
  entity state column stays `idle` because `send_to_entity` bypasses
  `spawn_entity`/`kill_entity` — this is expected, not a bug. Token
  usage is still recorded for these one-shot sends; the `_record_usage`
  hook fires in `send_to_entity` after `send_prompt` returns.
- **No worker task consumption** — the tasks table exists but workers
  don't exist yet in 2b. `claim_next()` with `SELECT … FOR UPDATE SKIP
  LOCKED` is marked as a TODO at `src/hive/bus/task_store.py` and lands
  in Sprint 3.
- **`/cost` shows API-equivalent cost only** — `total_cost_usd` comes
  straight from the `claude -p` result event and is labeled as
  "equivalent API cost (covered by Max subscription)". It is not money
  actually spent. Token counts are the real accountability number.
- **Local-only repo** — not pushed to GitHub yet.
