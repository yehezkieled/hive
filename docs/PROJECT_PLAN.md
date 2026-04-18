# Hive — Project Plan

> **Loop**: RALPH (Read → Ask → List → Plan → **Halt** — awaiting approval)
> **Approach**: Iterative — Sprint 0+1 detailed now. After building, we detail-plan the next batch using what exists.
> **Persistent copy**: Saved to Hive repo as `docs/PROJECT_PLAN.md` during Sprint 0

## Context

Building a multi-maestro AI agent orchestration platform ("Hive") natively on Claude Code, replacing OpenClaw. Spec locked 2026-04-02. The goal: a full remote AI workforce accessible from Telegram, maximizing the Anthropic Max subscription.

Each entity (maestro, team lead, worker) = a `claude -p` subprocess. A long-running Python asyncio orchestrator manages lifecycle, message routing, and Telegram integration.

## Decisions

- **Name**: Hive
- **Telegram**: Use Wonder bot (kmuawetfae_bot) — token goes in `.env`. DO NOT touch Lona (active Claude Code session)
- **GitHub**: User has an account — needs `gh auth login` + `git config` on VPS

## Prerequisites (before Sprint 0)

```bash
sudo apt install python3.12-venv python3-pip gh
# Then: gh auth login, git config --global user.name/email
# Then: create .env with TELEGRAM_BOT_TOKEN=<wonder token>
```

---

## Architecture Overview

```
YOU (Telegram / Local CLI / Future: Web App)
 │
 v
[Hive Orchestrator] ── long-running Python asyncio process
 │
 ├── TelegramBridge ── python-telegram-bot (Bot API polling)
 ├── MessageRouter ── asyncio.Queue per entity (real-time)
 ├── MessageStore ── SQLite → PostgreSQL (Sprint 2)
 ├── ProcessManager ── spawns/kills claude -p subprocesses
 │
 ├── [Maestro: "dev"] ── claude -p --output-format stream-json
 │    ├── [Lead: "backend"] ── claude -p (worktree)
 │    │    ├── [Worker: "coder-1"] ── claude -p (worktree)
 │    │    └── [Worker: "coder-2"] ── claude -p (worktree)
 │    └── [Lead: "ops"] ── claude -p
 │
 ├── [Maestro: "pa"] ── claude -p
 │    └── Teams...
 │
 └── [Vault] ── isolated, no bash/sudo, payment APIs only
```

### Core design decisions (apply to all sprints)
- **Orchestrator = Python process** — not a Claude session. Manages everything.
- **Message transport = asyncio.Queue** (real-time) + persistent store (SQLite → PostgreSQL)
- **Telegram = direct Bot API** via `python-telegram-bot` (not MCP plugin — full control)
- **Each entity = one `claude -p` subprocess** with `--output-format stream-json`
- **Max concurrent sessions = calculated per sprint** (VPS is 4vCPU/8GB, each CLI ~100MB)

---

## Sprint 0 — Repo + CI

**Goal**: Clean project scaffold, CI pipeline, ready to code.

### Files
```
hive/
├── pyproject.toml
├── README.md
├── .gitignore
├── .python-version                # 3.12
├── .github/workflows/ci.yml      # ruff + pytest
├── docs/
│   └── PROJECT_PLAN.md            # this plan (persistent copy)
├── src/hive/
│   └── __init__.py
├── tests/
│   └── __init__.py
├── personalities/
│   └── _template.md
└── data/                          # gitignored — runtime SQLite, logs
```

### Dependencies
- Runtime: `aiosqlite`, `python-telegram-bot`
- Dev: `pytest`, `pytest-asyncio`, `pytest-cov`, `ruff`

### CI
- ruff check + format
- pytest (skip `@pytest.mark.integration`)

### Deliverable
- GitHub repo with passing CI, `pip install -e ".[dev]"` works

---

## Sprint 1 — MVP (Single Maestro + Single Team)

**Goal**: Send a message from Telegram → maestro processes it → response back to Telegram.

**Builds on**: Sprint 0 (repo structure)

### New modules

| Module | Path | Purpose |
|--------|------|---------|
| Entity Model | `src/hive/models/entity.py` | EntityState enum, Entity dataclass, state machine |
| Maestro | `src/hive/models/maestro.py` | Maestro subclass |
| Worker | `src/hive/models/worker.py` | WorkerAgent subclass |
| TeamLead | `src/hive/models/team_lead.py` | Stub for Sprint 3 |
| Claude Session | `src/hive/process/claude_session.py` | Wraps `claude -p` subprocess (stdin/stdout/kill) |
| Process Manager | `src/hive/process/manager.py` | Spawn/track/kill entities, enforce concurrency limits |
| Worktree Manager | `src/hive/process/worktree.py` | `git worktree add/remove` for isolated agent work |
| Message Store | `src/hive/bus/store.py` | SQLite with WAL mode, async via aiosqlite |
| Message Router | `src/hive/bus/router.py` | asyncio.Queue per entity + logs to store |
| Command Parser | `src/hive/telegram/commands.py` | Parse `/status`, `/m:dev`, `/kill`, etc. |
| Telegram Bridge | `src/hive/telegram/bridge.py` | Bot API polling, auth by user ID, send/receive |
| Local CLI | `src/hive/cli/local.py` | readline-based CLI for testing without Telegram |
| Entry Point | `src/hive/__main__.py` | Wire everything, asyncio.run(), graceful shutdown |
| Config | `src/hive/config.py` | Paths, defaults, model names, env vars |

### Build order within Sprint 1
| Phase | What | Depends on | Parallel? |
|-------|------|------------|-----------|
| 1 | Entity model + state machine | — | Yes |
| 2 | Claude session wrapper | — | Yes |
| 3 | Message store (SQLite) | — | Yes |
| 4 | Command parser | — | Yes |
| 5 | Message router | store | No |
| 6 | Process manager + worktrees | entity, session | No |
| 7 | Local CLI | router, manager, commands | No |
| 8 | Telegram bridge | router, commands | No |
| 9 | Main entry point | everything | No |

### Key technical risk
`--input-format stream-json` for multi-turn sessions needs empirical verification. Fallback: one-shot `claude -p` with `--resume <session-id>` for conversation continuity.

### Personality config format
```markdown
# Maestro: Dev
## Identity
- **Name**: Dev
- **Role**: maestro
- **Model**: sonnet
## System Prompt
You are Dev, a software engineering maestro...
## Tools
- allowedTools: Bash Read Write Edit Grep Glob
```

### Commands available after Sprint 1
`/status`, `/health`, `/m:<name>`, `/kill <name>`, `/maestros`, `/org`, `/comms`

### Verification
1. `python -m hive` starts orchestrator
2. `/m:dev hello` from Telegram → maestro spawns, responds
3. `/status` shows maestro PID + uptime
4. `/kill dev` stops subprocess
5. Messages logged in SQLite

---

## Sprint 2 — PostgreSQL (Task Queue, State, Logs, Tokens)

> **Split 2026-04-15**: The original locked spec bundled the PG port, task queue,
> token tracking, audit log, `/cost`, and `/tasks` into one sprint. Per the
> iterative-planning preference, it was split into **Sprint 2a** (PG port +
> entity persistence — the risky infra foundation) and **Sprint 2b** (tasks,
> tokens, audit log, `/cost` + `/tasks` — four additive features on top of a
> stable DB layer). 2a is complete; 2b is detail-planned after 2a lands.

**Goal**: Replace SQLite with PostgreSQL for production-grade coordination.

**Builds on**: Sprint 1 (`MessageStore` interface, `Entity` state tracking)

### Why now (not later)
SQLite works for MVP but doesn't scale to multi-maestro coordination, concurrent writes from multiple agents, or the structured queries needed for token tracking and observability. Moving to PG early means every sprint after this gets proper persistence for free.

### New/changed modules
| Module | Change | Purpose |
|--------|--------|---------|
| `bus/store.py` | Rewrite | PostgreSQL via `asyncpg` (swap out aiosqlite) |
| `bus/migrations/` | New | SQL migration files (alembic or raw SQL) |
| `models/task.py` | New | Task model with priority, status, assignment |
| `tracking/tokens.py` | New | Token usage logging per entity per request |
| `tracking/costs.py` | New | Cost calculation (subscription vs API overflow) |

### Database schema (new tables)
```sql
-- Extends messages table from Sprint 1
-- Adds:
tasks(id, title, description, priority, status, assigned_to, maestro, team, created_at, completed_at)
entities(id, name, role, state, model, pid, started_at, personality_path)
token_usage(id, entity_id, task_id, input_tokens, output_tokens, model, cost_usd, timestamp)
audit_log(id, actor, action, target, details, timestamp)
```

### Prerequisites
```bash
# Install PostgreSQL on VPS (or use Docker)
sudo apt install postgresql postgresql-client
# Or: docker run -d --name hive-pg -e POSTGRES_PASSWORD=... -p 5432:5432 postgres:16
```

### Dependencies added
- `asyncpg` (async PostgreSQL driver)
- `alembic` (migrations) — or keep it simple with raw SQL migration files

### Verification
- All Sprint 1 features work identically with PostgreSQL backend
- `/cost` command shows token usage
- `/tasks` command shows task queue

---

## Sprint 2a — PG Port + Entity Persistence (2026-04-15, DONE)

**Goal**: Replace SQLite with PostgreSQL and make entities survive orchestrator
restart. The foundation 2b builds on.

**Why this split**: the PG port is the risky/boring bit (new infra, new driver,
new test setup). Entity persistence was grouped in because a backend swap has
no user-visible benefit on its own — entities still vanish on Ctrl+C. Getting
2a green means "hive survives restart", which is a real checkpoint.

**Environment note**: PostgreSQL is NOT installed on the VPS. Docker IS (n8n
runs in it), so we run PG in a container rather than `apt install postgresql`.

### Phases (all complete)

**Phase A — Pre-sprint cleanup.** Committed WIP `--verbose` / stream-json
parsing fix (`entity.py` + `claude_session.py`), dropped `--bare` expectation
in `test_entity.py::TestEntityCLIArgs::test_basic_args`, untracked `.env` via
`git rm --cached` + `.gitignore`, seeded `.env.example`, backed up
`data/hive.db` → `data/hive.db.sqlite-bak`.

**Phase B — Postgres infrastructure.** `docker-compose.yml` at repo root:
`postgres:16-alpine`, `127.0.0.1:5433:5432` (host-side 5433 avoids conflict
with any future system PG; 127.0.0.1 binding keeps it off the internet),
named volume `hive_pgdata`, `pg_isready` healthcheck. Replaced `aiosqlite`
with `asyncpg>=0.29` in `pyproject.toml`; added `testcontainers[postgres]>=4.0`
as a dev dep. `config.py` now builds `POSTGRES_DSN` from `POSTGRES_HOST/PORT/
DB/USER/PASSWORD` env vars (defaults `127.0.0.1:5433/hive` as user `hive`).

**Phase C — Migration runner.** Raw-SQL migrations over Alembic (Alembic is
overkill for one env, five tables). `src/hive/bus/migrations/runner.py` —
~60-line loader: creates `schema_migrations(version INT PK, applied_at
TIMESTAMPTZ)`, globs `NNN_*.sql`, runs unseen ones in a transaction per file,
records the version. Idempotent on every startup. `001_messages.sql`:
`BIGSERIAL` PK, `TIMESTAMPTZ` timestamp (replacing the SQLite float epoch),
`JSONB` metadata (replacing the TEXT-encoded JSON), indexes on
`(recipient, status)`, `conversation_id`, and `timestamp DESC`.

**Phase D — Port `MessageStore` to asyncpg.** Hard-replace, no abstraction
layer (we're not shipping two backends). Same public interface as before, so
`router.py` didn't notice. `asyncpg.create_pool(dsn, min_size=2, max_size=10,
init=_init_connection)`. `_init_connection` registers a JSONB codec
(`encoder=json.dumps, decoder=json.loads, schema="pg_catalog"`) so Python
dicts auto-encode — asyncpg doesn't do this by default. Placeholders `?` →
`$1/$2/…`, `cursor.lastrowid` → `INSERT … RETURNING id` via `fetchval`,
`since: float | None` → `since: datetime | None`, `metadata: str | None` →
`metadata: dict | None`. `connect()` runs the migrations at the end. Rows
come back as `asyncpg.Record`; we materialize as `[dict(row) for row in rows]`
to keep the existing callers happy.

**Phase E — Test infrastructure.** Session-scoped PG container in
`conftest.py` (`PostgresContainer("postgres:16-alpine")`) — per-test spin-up
is ~2s × N tests, unusable. testcontainers returns a SQLAlchemy-style URL
(`postgresql+psycopg2://…`); stripped to `postgresql://…` for asyncpg.
Function-scoped `store` fixture `TRUNCATE`s `messages` and `entities` between
tests (fast, keeps pool warm, resets `BIGSERIAL`). Removed the per-file
SQLite fixtures in `test_store.py`, `test_router.py`, `test_process_manager.py`
— everything pulls from conftest now. Test suite: 74 passing (64 original +
10 new entity_store) in ~14s.

**Phase F — Entity persistence.** `002_entities.sql`:
```sql
CREATE TABLE entities (
    name TEXT PRIMARY KEY,
    role TEXT NOT NULL,
    state TEXT NOT NULL,
    model TEXT NOT NULL,
    personality_path TEXT,
    pid INTEGER,
    started_at TIMESTAMPTZ,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX idx_entities_role ON entities (role);
CREATE INDEX idx_entities_state ON entities (state);
```
`src/hive/bus/entity_store.py` — new `EntityStore(pool)` with `upsert` /
`load` / `all` / `delete`. Kept separate from `MessageStore` so each class
stays focused. `upsert` uses `INSERT … ON CONFLICT (name) DO UPDATE`.
`_row_to_entity` forces restored entities to `EntityState.IDLE` — the state
machine doesn't allow `STOPPED → STARTING`, but `IDLE → STARTING` is the
canonical spawn path, so IDLE is the right restoration state. The live state
at shutdown is meaningless after a restart anyway (the subprocess died with
the parent).

`ProcessManager` gained an optional `entity_store` constructor arg and a
`_persist(entity)` helper that calls `upsert` if the store is set. DB writes
are at the manager level, NOT inside `Entity.transition_to()` — that's a sync
dataclass method, and keeping the DB call out of it keeps the Entity itself
testable without a DB. `_persist` is called after STARTING, after RUNNING,
after ERROR (in `spawn_entity` and `health_check`), and after STOPPED (in
`kill_entity`). A new `restore(entity)` method adds the entity to `_entities`
and registers it with the router without spawning a subprocess.

`__main__.py` wiring: after `store.connect()`, build `EntityStore(store.pool)`,
pass it to `ProcessManager`, loop `for e in await entity_store.all():
process_manager.restore(e)`, then only register the default maestro if it
wasn't already restored from a previous session (first-run safe).

### Critical files

**Edited**: `pyproject.toml`, `.gitignore`, `src/hive/config.py`,
`src/hive/bus/store.py`, `src/hive/bus/router.py`, `src/hive/process/manager.py`,
`src/hive/__main__.py`, `src/hive/models/entity.py`,
`src/hive/process/claude_session.py`, `tests/test_entity.py`,
`tests/test_store.py`, `tests/test_router.py`, `tests/test_process_manager.py`,
`tests/conftest.py`, `docs/PROJECT_PLAN.md`.

**Created**: `docker-compose.yml`, `.env.example`,
`src/hive/bus/migrations/__init__.py`, `src/hive/bus/migrations/runner.py`,
`src/hive/bus/migrations/001_messages.sql`,
`src/hive/bus/migrations/002_entities.sql`, `src/hive/bus/entity_store.py`,
`tests/test_entity_store.py`.

### Deliberately out of scope for 2a

- No task queue, `/tasks` command, `tasks` table (→ 2b)
- No token tracking, `/cost` command, `token_usage` table (→ 2b)
- No audit log, `audit_log` table (→ 2b)
- No SQLite → PG data migration (starting fresh; `hive.db.sqlite-bak` insurance)
- No re-spawning of running processes on restart (only structural state)
- No Alembic (raw SQL until the second environment exists)
- No store interface/protocol (one backend)

### Verification

1. Working tree clean, `.env` untracked, `pytest -v` green.
2. `docker compose up -d postgres` → `docker compose ps` shows healthy.
3. `python -m hive` → migrations run on startup (`001_messages`, `002_entities`
   logged).
4. Full suite: 74 tests passing in ~14s against the testcontainer.
5. Telegram round-trip persists to `messages` with proper `TIMESTAMPTZ`.
6. Restart survives: `Ctrl+C` → `python -m hive` again → `/maestros` lists the
   previously-registered maestros without re-registration → respawn works.

---

## Sprint 2b — Tokens, Tasks, Audit Log (2026-04-15, DONE)

**Goal**: Three additive features on top of the stable 2a foundation — token
tracking with `/cost`, a minimal persistent task queue with `/task`/`/tasks`,
and an audit log with `/audit`. Each is independent, shares the same
migration → store → wire → command → tests pattern, and lands in its own
commit so a future bisect stays clean.

**Why this order**: tokens first because they touch the fewest files and
validate the end-to-end pattern; tasks next to add a new table + new
commands; audit last because it's cross-cutting and needs 2b's other hook
points to be stable. Each phase was manually verified against a live hive
restart before committing.

### Phases (all complete)

**Phase A — Pre-sprint cleanup.** `git status` clean except a not-yet-
committed `docs/DEPLOYMENT.md` from the 2a runbook session — folded into
the Phase E docs commit rather than landing as a standalone pre-sprint
commit. `docker compose ps` confirmed `hive-postgres` healthy. Baseline:
`pytest -v` 74 green, `ruff check` clean.

**Phase B — Token tracking.** Captured a real `claude -p --verbose
--output-format stream-json` `result` event first to pin the field names
before writing the migration. Anthropic's API names
(`input_tokens`, `output_tokens`, `cache_creation_input_tokens`,
`cache_read_input_tokens`) go into the column names verbatim so the
session-parsing code can pass the usage dict through without renaming.
`003_token_usage.sql`: `BIGSERIAL` PK, `NUMERIC(14, 8)` cost (widened
from the initial `(10, 6)` sketch after `test_record_stores_all_fields`
caught the truncation of `0.03958525` to `0.039585`), indexes on
`(entity_name, recorded_at DESC)` and `(recorded_at DESC)`.
`src/hive/bus/token_store.py`: `TokenStore` with `record` / `totals` /
`recent` mirroring the `EntityStore` shape (takes `pool`, not a DSN).
`TypedDict UsageEvent` for the usage-dict shape. `ClaudeSession` extended
to capture the `usage` sub-object + `total_cost_usd` from the result
event onto `self._last_usage`, exposed via a `last_usage` property.
`ProcessManager` gained an optional `token_store` arg and a
`_record_usage(entity, session)` fire-and-continue hook called in
`send_to_entity` after `send_prompt` returns — fold the entity's
canonical `model` into the usage dict at the record site.
`telegram/bridge.py`: new `/cost [24h|7d|30d]` command formats totals
with a labeled `$X.XXXX equivalent API cost (covered by Max subscription)`
line, because `total_cost_usd` is the API-equivalent, not money actually
spent. `_parse_window` defaults to 24h on unrecognised input.
9 `TokenStore` tests + 3 `ClaudeSession` usage-capture tests.

**Phase C — Task queue.** Minimal CRUD, no worker consumption yet —
workers don't exist in 2b so there's nothing to claim from the queue.
`004_tasks.sql`: `BIGSERIAL` PK, text `status` with pending-default,
integer `priority` (0 urgent → 4 backlog), nullable `assigned_to`,
namespaced `created_by` (`user:<tg>` | `system` | `entity:<name>`),
indexes on `(status, priority, created_at)` and a partial index on
`assigned_to WHERE assigned_to IS NOT NULL`. No FK to `entities(name)`
— cascade semantics aren't obvious and tasks should outlive entity
deletes. `src/hive/models/task.py`: `Task` dataclass + `TaskStatus`
enum. No state machine: `status` is a plain enum field (unlike
`Entity`, tasks don't have explicit valid-transition rules worth
policing at the dataclass level). `src/hive/bus/task_store.py`:
`TaskStore` with `create` / `get` / `list` (status filter + priority
sort) / `update_status` (sets `completed_at` when moving to
`COMPLETED`, leaves it null for `CANCELLED`). `# TODO(sprint-3)`
marker at the seam where `claim_next()` with `SELECT … FOR UPDATE SKIP
LOCKED` will land once workers exist. Parser: added `"task"` to
`targeted_commands` so the subcommand lands in `Command.target`
(`/task add "foo"` → `target="add"`, `args='"foo"'`). Bridge dispatch:
`_execute_task` handles add/done/cancel with a tiny `_strip_quotes`
helper for the title and `_parse_task_id` for the id; `_format_tasks_list`
shows pending + in-progress tasks one-per-line. `TaskStore` wires into
`TelegramBridge` directly (not `ProcessManager`) — tasks are purely
user-facing in 2b, so growing the manager surface for them would be
wrong. 12 `TaskStore` tests + 7 new parser cases.

**Phase D — Audit log.** One journal, one insert per event. Over-logging
is cheaper to filter later than under-logging is to reconstruct, so even
read-only `/status` and `/maestros` get logged. `005_audit_log.sql`:
`BIGSERIAL` PK, text `actor`/`action`/`target`, `JSONB` details,
`TIMESTAMPTZ` timestamp, indexes on `timestamp DESC`,
`(actor, timestamp DESC)`, and `action`. `action` is namespaced
(`command.<name>`, `entity.<state>`, `task.<op>`) so per-category
readouts are a single `LIKE 'entity.%'` filter. `src/hive/bus/audit_log.py`:
`AuditLog` with `record` (fire-and-continue — DB errors logged and
swallowed so audit failure never takes down the caller's work) and
`recent` (optional `action_prefix` arg). `details` round-trips as a
dict via `json.dumps` on write and a `json.loads` decode on read (the
`_row_to_dict` helper handles the JSONB-as-str case). `ProcessManager`
gained a separate `_audit` helper parallel to `_persist` — the two
track different things (DB roster row vs. event stream), so fusing
them into one hook would have conflated concerns. Emits `entity.spawn`
on successful spawn, `entity.error` on spawn failure (with `phase:
"spawn"`) or health-check-dead (with `phase: "health"`), and
`entity.kill` on `kill_entity`. `restore()` is deliberately NOT audited
— structural re-registration is not a real state transition.
`telegram/bridge.py`: new `audit_log` arg, `actor = f"user:{tg_id}"`
captured once in `_handle_message` and threaded into `_execute_command`.
Every non-empty command writes `command.<name>` once after dispatch.
Inside `_execute_task`, task-level events (`task.create`,
`task.update_status`) are emitted separately so a `LIKE 'task.%'`
filter returns all task changes regardless of how they were initiated
— future non-bridge sources get the same treatment for free. New
`/audit [entity|command|task]` command formats the last N events with
an optional category prefix; bare `/audit` shows everything. 8
`AuditLog` CRUD + prefix-filter + JSONB round-trip tests (including a
`_BrokenPool` test confirming the fire-and-continue contract holds),
2 parser cases for `/audit`, 2 `ProcessManager` tests for the
`entity.kill` / `entity.error` hooks.

**Phase E — Docs + verification.** Sprint 2b section appended here;
`docs/DEPLOYMENT.md` updated with the three new tables + `/cost` /
`/tasks` / `/audit` commands (also picks up the pre-existing
not-yet-committed deployment runbook from the 2a session so 2b lands
one clean docs commit instead of two).

### Deliberately out of scope for 2b

- No worker consumption of tasks — `claim_next()` with `SELECT … FOR
  UPDATE SKIP LOCKED` stays commented until Sprint 3 when workers exist.
- No real-API cost routing or per-provider breakdown — `/cost` shows
  Anthropic API-equivalent numbers only, labeled as covered by Max.
- No vault audit — vault is payment-critical and needs stricter
  guarantees than a best-effort journal; deferred to Sprint 6.
- No SQLite → PG migration of historical token/task/audit data — no
  such data exists.
- No `/audit` pagination or advanced filtering beyond a single
  category prefix — enough to explore from Telegram; `psql` is the
  real query surface.

### Critical files

**Created (all phases)**: `src/hive/bus/migrations/003_token_usage.sql`,
`src/hive/bus/migrations/004_tasks.sql`,
`src/hive/bus/migrations/005_audit_log.sql`, `src/hive/bus/token_store.py`,
`src/hive/bus/task_store.py`, `src/hive/bus/audit_log.py`,
`src/hive/models/task.py`, `tests/test_token_store.py`,
`tests/test_task_store.py`, `tests/test_audit_log.py`.

**Edited**: `src/hive/process/claude_session.py` (capture `last_usage`),
`src/hive/process/manager.py` (`token_store` + `audit_log` args,
`_record_usage` + `_audit` helpers, entity.\* audit emissions),
`src/hive/telegram/commands.py` (`"task"` in `targeted_commands`,
docstring), `src/hive/telegram/bridge.py` (new `token_store` /
`task_store` / `audit_log` args, `/cost` / `/task` / `/tasks` /
`/audit` branches, actor plumbing), `src/hive/__main__.py` (construct
and wire the three new stores), `tests/conftest.py` (three new
fixtures + three new `TRUNCATE` lines), `tests/test_claude_session.py`
(usage-capture assertions), `tests/test_commands.py` (parser cases
for 2b commands), `tests/test_process_manager.py` (audit-hook
coverage), this file, `docs/DEPLOYMENT.md`.

### Verification

1. `pytest -v` → 117 passing (up from 74 after 2a).
2. `ruff check src/ tests/` clean.
3. `docker compose ps` → `hive-postgres` healthy.
4. Restart hive. Log shows:
   - `Running migration 003_token_usage.sql`
   - `Running migration 004_tasks.sql`
   - `Running migration 005_audit_log.sql`
   - `Restored persisted entity: dev`
5. `psql hive -c "SELECT version, filename FROM schema_migrations
   ORDER BY version"` → 5 rows, through `005_audit_log.sql`.
6. End-to-end from Telegram:
   - `/task add "test task"` → confirmation + task id
   - `/tasks` → shows the task
   - plain `hi` → dev responds → `/cost` shows non-zero tokens
   - `/audit` → shows the preceding commands, newest first
7. `psql hive -c "SELECT action, count(*) FROM audit_log GROUP BY action"`
   → sensible distribution (`command.task`, `command.message`,
   `command.cost`, `entity.spawn`, etc.).

---

## Sprint 3a — Teams, Multi-Turn Sessions, Worker Lifecycle (2026-04-16, DONE)

**Goal**: Team model, resume-based multi-turn sessions (`--resume`),
worktree integration for workers, task claiming with `SELECT ... FOR
UPDATE SKIP LOCKED`, and Telegram commands for team/worker management.

**Builds on**: Sprint 2b (task queue, entity persistence, audit log)

### Phases (all complete)

**Phase 1 — Resume-based multi-turn sessions.** Added `session_id` field
to `Entity`, persisted via `EntityStore` (migration `006`). `send_to_entity`
now passes `--resume <session_id>` on subsequent calls so entities remember
their conversation context. Verified with mocked `ClaudeSession` tests:
first call has no `--resume`, second call includes it, `kill_entity` clears it.

**Phase 2 — Team model + hierarchical entities.** New `Team` dataclass
(`src/hive/models/team.py`). Maestro gained `teams: dict[str, Team]` with
`create_team` / `get_team` / `remove_team` methods. `TeamLead` fleshed
out: `team_name`, `maestro_name`, `max_workers`. `WorkerAgent` fleshed
out: `team_name`, `lead_name`, `task_id`. Naming convention:
`maestro.team` for leads, `maestro.team.worker` for workers (matches
existing `/t:` and `/a:` parser patterns). `EntityStore._row_to_entity`
now polymorphic — returns `Maestro`, `TeamLead`, or `WorkerAgent` based
on role column. Migration `007` adds `parent_name` and `team_name`
columns with index. `ProcessManager` gained `create_team`, `spawn_worker`,
`kill_team` methods with permission-aware validation.

**Phase 3 — Worktree integration.** `spawn_worker` creates a git worktree
via `WorktreeManager` (branch: `hive/<full_name>`) and sets `worker.worktree_path`.
`kill_entity` removes the worktree for workers. Tested with mocked
`WorktreeManager`.

**Phase 4 — Task claim_next().** `TaskStore.claim_next(entity_name)` uses
`SELECT ... FOR UPDATE SKIP LOCKED` to atomically claim the highest-priority
pending task. Tested: basic claim, empty queue, non-pending skip, sequential
claims get different tasks.

**Phase 5 — Telegram commands.** Parser extended: `team`, `worker`, `swarm`
added to `targeted_commands`. Bridge wired:
- `/team create|list|kill` — team CRUD
- `/teams` — list all teams
- `/worker spawn <team> [name]` — spawn worker
- `/worker kill <name>` — kill worker
- `/t:dev.backend <msg>` — route message to team lead
- `/a:dev.backend.w1 <msg>` — route message to worker
- `/org` improved to show tree hierarchy (maestro → team → lead → workers)

**Phase 6 — Hierarchy restore.** `ProcessManager.rebuild_hierarchy()` reconstructs
`Maestro.teams` and `TeamLead.workers` from restored entities after restart.
Called from `__main__.py` after entity restore loop.

### Critical files

**Created**: `src/hive/models/team.py`, `src/hive/bus/migrations/006_entity_session_id.sql`,
`src/hive/bus/migrations/007_entity_hierarchy.sql`, `tests/test_team.py`.

**Edited**: `src/hive/models/entity.py` (+`session_id` field),
`src/hive/models/maestro.py` (teams dict, team methods),
`src/hive/models/team_lead.py` (hierarchy fields),
`src/hive/models/worker.py` (hierarchy fields + task_id),
`src/hive/process/manager.py` (resume logic, team/worker methods,
worktree integration, hierarchy rebuild),
`src/hive/bus/entity_store.py` (polymorphic restore, new columns),
`src/hive/bus/task_store.py` (`claim_next()`),
`src/hive/telegram/commands.py` (new targeted commands),
`src/hive/telegram/bridge.py` (team/worker handlers, improved /org),
`src/hive/__main__.py` (`rebuild_hierarchy()` call).

### Verification

1. `pytest -v` → 168 passing (up from 117 after 2b).
2. `ruff check src/ tests/` clean.
3. All 51 new tests cover: session_id roundtrip, resume flag in CLI args,
   Team/TeamLead/WorkerAgent hierarchy, polymorphic EntityStore restore,
   create_team/spawn_worker/kill_team, worktree create/remove,
   claim_next atomicity, command parsing, hierarchy rebuild.

### Deliberately out of scope for 3a

- Mode switching (`/mode`) — Sprint 3b
- Loop switching (`/loop`) — Sprint 3b
- Priority preemption (P0 bumps P4) — Sprint 3b
- Swarm mode (`/swarm`) — Sprint 3b
- LocalCLI mirroring of new commands — deferred

---

## Sprint 3b — Modes, Loops, Priority, Swarm, Session Utilities (2026-04-16, DONE)

**Goal**: Permission mode switching, workflow loop switching, priority system
(P0-P4) with preemption, swarm mode, and session utilities (/compact, /reset).

**Builds on**: Sprint 3a (teams, multi-turn sessions, worker lifecycle)

### Phases (all complete)

**Phase 1 — Permission mode switching.** Added `set_permission_mode()` on
Entity with `PERMISSION_MODES` dict mapping user-facing names to CLI flags:
`plan` → `--permission-mode plan`, `edit` → `--permission-mode default`,
`auto` → `--permission-mode bypassPermissions`. Persisted via `permission_mode`
column in entities table (migration `008_entity_modes.sql`).

**Phase 2 — Loop switching.** New `src/hive/process/loops.py` with
`LOOP_PROMPTS` dict defining four execution modes: `ralph` (Read, Ask, List,
Plan, Halt), `yolo` (execute immediately), `plan-act-observe` (Plan → Act →
Observe cycle), `build-test-refine` (Build → Test → Refine cycle).
`set_loop_mode()` on Entity validates and stores the selection. Loop prompt
injected via `--append-system-prompt` in `build_cli_args()`. Persisted via
`loop_mode` column (migration 008).

**Phase 3 — Priority system.** `current_priority` field (int, 0-4) on Entity.
`/priority P0 "title"` creates a task with the given priority.
`_preempt_for_priority()` in ProcessManager checks if a higher-priority entity
needs a slot and can pause lower-priority ones. Migration 008 adds
`current_priority`, `worktree_path`, and `task_id` columns.

**Phase 4 — Telegram commands.** `/mode`, `/loop`, `/priority`, `/swarm`,
`/compact`, `/reset` added to bridge dispatch. `/swarm <team> <goal>` sends
goal to all workers in a team. `/compact <entity>` summarizes context then
resets session with the summary. `/reset <entity>` kills and re-registers
with cleared session_id.

### Critical files

**Created**: `src/hive/process/loops.py`, `src/hive/bus/migrations/008_entity_modes.sql`,
`tests/test_loops.py`.

**Edited**: `src/hive/models/entity.py` (permission_mode, loop_mode, current_priority
fields, set_permission_mode, set_loop_mode, build_cli_args updated),
`src/hive/process/manager.py` (_preempt_for_priority), `src/hive/telegram/bridge.py`
(6 new command handlers), `src/hive/telegram/commands.py` (new targeted_commands),
`src/hive/bus/entity_store.py` (new columns).

### Verification

1. `pytest -v` → 191 passing (up from 168 after 3a).
2. `ruff check src/ tests/` clean.

---

## Sprint 4+5 — Multi-Maestro, Permissions, Personality, Model Switching (2026-04-16, DONE)

**Goal**: Multiple maestros, inter-agent permission hierarchy, personality
hot-reload, broadcast messaging, and per-entity model switching.

**Builds on**: Sprint 3b (mode/loop switching, priority system)

### Phases (all complete)

**Phase 1 — Multi-maestro.** `register_maestro()` method on ProcessManager.
`/new maestro <name> [model]` command creates additional maestros (default
model: sonnet). Each maestro is independent with its own org.

**Phase 2 — Permission hierarchy.** New `src/hive/bus/permissions.py` with
`can_message(sender_role, sender_name, recipient_role, recipient_name) -> bool`.
Uses dotted naming convention: `dev.backend.w1` belongs to lead `dev.backend`,
which belongs to maestro `dev`. Maestro → any entity in own org (shared name
prefix). Lead → own workers + parent maestro. Worker → own lead only.
Cross-org messaging denied.

**Phase 3 — Personality reload.** `/personality reload <entity>` re-reads the
.md personality file from disk and applies changes without restart.

**Phase 4 — Broadcast.** `/broadcast <message>` sends to all registered
entities via `router.broadcast()`.

**Phase 5 — Model switching.** `/model <opus|sonnet|haiku> [entity]` changes
entity's model at runtime. Validated against allowed set. Persisted to DB.

### Critical files

**Created**: `src/hive/bus/permissions.py`, `tests/test_permissions.py`.

**Edited**: `src/hive/process/manager.py` (register_maestro),
`src/hive/telegram/bridge.py` (_execute_new, _execute_personality,
_execute_broadcast, _execute_model), `src/hive/telegram/commands.py`
(new, personality, broadcast, model in targeted_commands).

### Verification

1. `pytest -v` → 207 passing (up from 191 after 3b).
2. `ruff check src/ tests/` clean.

---

## Sprint 6 — Vault Entity with Security-Gated Approval Flow (2026-04-16, DONE)

**Goal**: Isolated, security-critical entity for financial operations.
Every action requires explicit user approval via Telegram.

**Builds on**: Sprint 4+5 (multi-maestro, personality system)

### Phases (all complete)

**Phase 1 — Vault model.** New `src/hive/models/vault.py`. `Vault(Entity)`
subclass with `role="vault"` and `disallowed_tools = ["Bash", "Write", "Edit"]`
hard-locked. Cannot run filesystem or shell operations. Cannot be killed by
non-user actors.

**Phase 2 — Vault store.** New `src/hive/bus/vault_store.py` with `VaultStore`
class: `create_action(vault_name, description, requester)`,
`approve(action_id)`, `deny(action_id)`, `pending(vault_name)`,
`log(vault_name, limit)`. Migration `009_vault_actions.sql` creates
`vault_actions` table (id, vault_name, description, requester, status
pending/approved/denied, created_at, resolved_at).

**Phase 3 — Telegram commands.** `/vault approve <id>`, `/vault deny <id>`,
`/vault status [name]`, `/vault log [name]`. Approval flow: entity creates
pending action → user approves/denies from Telegram.

### Critical files

**Created**: `src/hive/models/vault.py`, `src/hive/bus/vault_store.py`,
`src/hive/bus/migrations/009_vault_actions.sql`, `tests/test_vault.py`,
`tests/test_vault_store.py`.

**Edited**: `src/hive/telegram/bridge.py` (_execute_vault handler),
`src/hive/telegram/commands.py` (vault in targeted_commands),
`src/hive/__main__.py` (VaultStore wiring), `tests/conftest.py`
(vault_store fixture + TRUNCATE).

### Verification

1. `pytest -v` → 216 passing (up from 207 after 4+5).
2. `ruff check src/ tests/` clean.

---

## Sprint 7 — File-Based Blueprint Storage and Search (2026-04-16, DONE)

**Goal**: Agents can save and search project blueprints — structured
knowledge documents with YAML frontmatter and markdown body.

**Builds on**: Sprint 2 (PostgreSQL), accumulated project data

### Phases (all complete)

**Phase 1 — BlueprintStore.** New `src/hive/knowledge/blueprints.py`.
`BlueprintStore(directory: Path)` with `save(title, content, tags) -> Path`,
`load(path) -> dict`, `list_all() -> list[dict]`,
`search(query, limit) -> list[dict]`. Blueprints stored as YAML-frontmatter
markdown files (title, tags, created_at in frontmatter; content in body).
Search is case-insensitive text matching on title + body. No embeddings or
vector search — file-based simplicity for Sprint 7.

**Phase 2 — Telegram commands.** `/blueprint save "title"` creates a new
blueprint. `/blueprint search <query>` does case-insensitive text search.
`/blueprint list` shows all blueprints with metadata.

### Critical files

**Created**: `src/hive/knowledge/__init__.py`, `src/hive/knowledge/blueprints.py`,
`tests/test_blueprints.py`.

**Edited**: `src/hive/config.py` (BLUEPRINTS_DIR), `src/hive/telegram/bridge.py`
(BlueprintStore import, _execute_blueprint), `src/hive/telegram/commands.py`
(blueprint in targeted_commands), `src/hive/__main__.py` (BlueprintStore wiring).

### Verification

1. `pytest -v` → 225 passing (up from 216 after Sprint 6).
2. `ruff check src/ tests/` clean.

---

## Sprint 8 — FastAPI Web Dashboard with htmx (2026-04-16, DONE)

**Goal**: Read-only web dashboard showing entity status, org tree, tasks,
cost, and audit log — polling-based via htmx.

**Builds on**: Everything (exposes all stores via JSON API)

### Phases (all complete)

**Phase 1 — FastAPI application.** New `src/hive/web/app.py` with
`create_app(process_manager, token_store, task_store, audit_log) -> FastAPI`
factory. Dependencies added: `fastapi`, `uvicorn`, `jinja2`.

**Phase 2 — JSON API endpoints.** Five GET endpoints:
`/api/status` (entity statuses), `/api/org` (org tree as JSON),
`/api/tasks` (open tasks), `/api/cost?window=24h|7d|30d` (token usage),
`/api/audit?limit=20` (recent audit events).

**Phase 3 — HTML dashboard.** htmx-powered template at
`src/hive/web/templates/dashboard.html`. Dark theme (slate/amber palette).
Auto-refresh: status every 5s, org every 5s, tasks every 10s, cost every 30s.
CSS Grid layout (2-col desktop, 1-col mobile).

**Phase 4 — Integration.** `__main__.py` conditionally starts uvicorn
alongside Telegram bridge when `WEB_PORT > 0` (env var, default 0 = disabled).

### Critical files

**Created**: `src/hive/web/__init__.py`, `src/hive/web/app.py`,
`src/hive/web/templates/dashboard.html`, `tests/test_web_api.py`.

**Edited**: `pyproject.toml` (fastapi, uvicorn, jinja2, httpx deps),
`src/hive/config.py` (WEB_PORT), `src/hive/__main__.py` (uvicorn startup).

### Verification

1. `pytest -v` → 233 passing (up from 225 after Sprint 7).
2. `ruff check src/ tests/` clean.

---

## Bug Fix — 5 Critical Bugs from Code Review (2026-04-16, DONE)

**Issues fixed**:

1. **Missing VaultStore/BlueprintStore wiring** — `__main__.py` was not
   passing `vault_store` or `blueprint_store` to `TelegramBridge`, so
   `/vault` and `/blueprint` commands crashed at runtime.

2. **Phantom entities on restart** — `kill_entity()` was not deleting the
   entity from the database, so killed entities reappeared after restart.
   Fixed by adding `await self.entity_store.delete(name)` to `kill_entity()`.

3. **JSONB double-encoding** — `AuditLog.record()` was calling `json.dumps()`
   on the details dict before passing to asyncpg, but the pool's JSONB codec
   already handles serialization. Result: double-encoded strings in the DB.
   Removed the manual `json.dumps()`.

4. **Direct entity registry access** — Code in `__main__.py` was writing
   directly to `process_manager._entities` dict, bypassing validation and
   persistence. Replaced with `register_maestro()` API.

5. **Dead code cleanup** — Removed unused `_format_event_row()` function
   from `audit_log.py`.

### Critical files

**Edited**: `src/hive/__main__.py`, `src/hive/process/manager.py`,
`src/hive/bus/audit_log.py`, `src/hive/telegram/bridge.py`.

### Verification

1. `pytest -v` → 233 passing (unchanged count; fixes, no new tests).
2. `ruff check src/ tests/` clean.

---

## GitHub Push + systemd Deployment (2026-04-17, DONE)

Between the bug fix commit and Sprint 9, the codebase was pushed to GitHub
for the first time and a systemd user service was set up for VPS persistence.

### What was done

1. **GitHub repository created** — `yehezkieled/hive` (private). All commits
   from Sprint 0 through the bug fix (`633f03e`) pushed to `origin/main`.

2. **CI workflow** — `.github/workflows/ci.yml` was already in the repo from
   Sprint 0. Runs on push/PR to `main`:
   - `ruff check src/ tests/` — lint
   - `ruff format --check src/ tests/` — formatting
   - `pytest -m "not integration" --cov=src/hive` — unit tests (no PG container)

3. **CI failure and fix** — First push triggered CI; `ruff format --check`
   failed on 15 files with formatting diffs. Fixed by running
   `ruff format src/ tests/`, committed as `34bbf98` ("Fix ruff formatting
   to pass CI"). Second CI run passed: lint ✓, tests ✓ (233 passing, 47s).

4. **systemd user service** — Created
   `~/.config/systemd/user/hive.service` for the VPS (DigitalOcean droplet,
   Tailscale network). Key configuration:
   - `Type=simple`, `Restart=on-failure`, `RestartSec=5`
   - `EnvironmentFile=/home/hezki/projects/hive/.env`
   - `ExecStart=.venv/bin/python -m hive`
   - `ExecStartPre=/bin/sleep 10` (wait for network/Tailscale on boot)
   - `KillSignal=SIGTERM`, `TimeoutStopSec=30`
   - Enabled with `loginctl enable-linger hezki` for boot persistence

### Commit

| Hash | Message |
|------|---------|
| `34bbf98` | Fix ruff formatting to pass CI |

### Verification

1. `gh run watch 24575078822 --exit-status` → CI green (lint + 233 tests in 47s).
2. `systemctl --user status hive` → service loaded and enabled.

---

## Sprint 9 — Inter-Agent Autonomous Messaging (2026-04-17, DONE)

**Goal**: Entities can send messages to other entities autonomously. When a
maestro or lead responds, it can include a `<hive_actions>` block with message
actions. The orchestrator extracts these, validates permissions, routes them,
and returns clean text to the user.

### Why this matters

Before Sprint 9, all communication flowed through the user via Telegram —
entities couldn't coordinate with each other. Now a maestro can delegate to
its leads, and leads can report back, without the user manually relaying
messages. This is the first step toward autonomous multi-agent workflows.

### Design decisions

- **XML tags, not tool_use**: Entities are `claude -p` subprocesses — we don't
  control tool definitions. XML tags in text output are the simplest reliable
  protocol. They're unambiguous, don't collide with prose, and easy to
  regex-extract.

- **Pull-based, not push**: Entities don't run in the background. They only
  act when prompted. Their responses can have *side effects* (queuing messages),
  but those messages are only delivered when the recipient is next prompted.
  This prevents infinite message loops.

- **Workers excluded from messaging prompt**: Only maestros and leads get the
  `MESSAGING_PROMPT` system prompt injection. Workers can only message their
  own lead, and that's better handled by the lead prompting them directly.

### Build phases

**Phase 1 — Action parser** (`src/hive/bus/actions.py`, new file)

Created the `Action` dataclass and `parse_actions()` function:

```python
@dataclass
class Action:
    type: str   # "message" for Sprint 9
    to: str     # recipient entity name
    text: str   # message content

def parse_actions(response: str) -> tuple[str, list[Action]]:
    """Extract <hive_actions> block, return (clean_text, actions)."""
```

- Regex `<hive_actions>\s*(.*?)\s*</hive_actions>` extracts the block
- Parses JSON array inside, creates `Action` objects
- Validates required fields `{type, to, text}` — skips incomplete entries
- Only recognizes `type: "message"` — unknown types are skipped with a warning
- Malformed JSON → warning + empty list (never crashes)
- Strips the `<hive_actions>` block from the returned clean text

Tests (`tests/test_actions.py`, 10 tests):
- `test_no_actions_returns_original_text`
- `test_single_message_action`
- `test_multiple_actions`
- `test_clean_text_strips_block`
- `test_clean_text_preserves_surrounding`
- `test_malformed_json_returns_empty_list`
- `test_missing_required_fields_skips_action`
- `test_unknown_action_type_skipped`
- `test_non_array_json_returns_empty`
- `test_mixed_valid_and_invalid_actions`

**Phase 2 — Pending message injection** (`src/hive/process/manager.py`)

Edited `send_to_entity()` to drain pending inter-agent messages before
sending the prompt. Uses the previously-unused `router.has_pending()` and
`router.get_next()` methods (built in Sprint 3a but never wired):

```python
# Before session.send_prompt(prompt):
pending = []
while self.router.has_pending(entity_name):
    msg = await self.router.get_next(entity_name, timeout=0.1)
    if msg:
        pending.append(f"[Message from {msg.sender}]: {msg.content}")
if pending:
    inbox = "\n".join(pending)
    prompt = f"You have pending messages from other entities:\n{inbox}\n\n---\n\n{prompt}"
```

Tests (3 in `TestPendingMessageInjection`):
- `test_pending_messages_prepended` — messages from queue appear in prompt
- `test_no_pending_prompt_unchanged` — no messages → prompt passed through
- `test_multiple_pending_all_included` — multiple messages all appear

**Phase 3 — Response action routing** (`src/hive/process/manager.py`)

After getting the response, `send_to_entity()` now parses actions, validates
permissions using `can_message()` (built in Sprint 4+5 but never wired),
routes messages via `router.route()`, and logs audit events:

```python
# After response = await session.send_prompt(prompt):
clean_text, actions = parse_actions(response)
self._last_routed_actions = []
for action in actions:
    if action.type == "message":
        recipient = self._entities.get(action.to)
        if not recipient:       # unknown entity → skip
            continue
        if not can_message(...): # permission check → skip
            continue
        await self.router.route(entity_name, action.to, action.text)
        self._last_routed_actions.append(action.to)
        await self._audit("message.autonomous", ...)
return clean_text  # stripped of <hive_actions> block
```

New imports added: `parse_actions` from `hive.bus.actions`,
`can_message` from `hive.bus.permissions`.

New attribute: `self._last_routed_actions: list[str]` — tracks recipients
of successfully routed messages for the Telegram bridge summary.

Tests (7 in `TestActionRouting`):
- `test_message_routed_to_recipient` — message lands in recipient's queue
- `test_permission_denied_blocks_routing` — worker → other team's lead blocked
- `test_unknown_recipient_handled` — non-existent entity skipped
- `test_clean_text_returned` — `<hive_actions>` stripped from response
- `test_routed_actions_tracked` — `_last_routed_actions` populated
- `test_no_actions_no_side_effects` — plain response has no routing
- `test_action_routing_writes_audit_event` — `message.autonomous` audit event

**Phase 4 — System prompt injection** (`src/hive/process/loops.py`,
`src/hive/models/entity.py`)

Added `MESSAGING_PROMPT` constant to `loops.py` — instructs entities on the
`<hive_actions>` protocol format and when to use it.

In `entity.py`, `build_cli_args()` now conditionally appends the messaging
prompt for maestro and lead roles:

```python
if self.role in ("maestro", "lead"):
    args.extend(["--append-system-prompt", MESSAGING_PROMPT])
```

Tests (3 in `TestMessagingPromptInjection`):
- `test_maestro_includes_messaging_prompt` — maestro gets 2 `--append-system-prompt` args
- `test_lead_includes_messaging_prompt` — lead gets messaging prompt
- `test_worker_excludes_messaging_prompt` — worker gets only loop prompt

**Phase 5 — Telegram bridge summary** (`src/hive/telegram/bridge.py`)

After `send_to_entity()` returns, the bridge checks
`process_manager._last_routed_actions` and appends a summary line:

```
--- Sent message to: dev.backend, dev.frontend
```

This gives the user visibility into autonomous message routing without
cluttering the entity's actual response.

### Infrastructure reused (previously unused)

These components were built in earlier sprints but had no callers until now:

| Component | File | Sprint built | What it does |
|-----------|------|-------------|--------------|
| `router.get_next()` | `bus/router.py:76-86` | 3a | Blocking consume from entity queue |
| `router.has_pending()` | `bus/router.py:88-92` | 3a | Check if queue has messages |
| `can_message()` | `bus/permissions.py:13-41` | 4+5 | Permission hierarchy validation |

### Files created/edited

| File | Action | Lines changed |
|------|--------|---------------|
| `src/hive/bus/actions.py` | **Created** | +75 |
| `src/hive/process/manager.py` | Edited | +40 |
| `src/hive/process/loops.py` | Edited | +11 |
| `src/hive/models/entity.py` | Edited | +6 |
| `src/hive/telegram/bridge.py` | Edited | +6 |
| `tests/test_actions.py` | **Created** | +114 |
| `tests/test_process_manager.py` | Edited | +284 |
| `tests/test_entity.py` | Edited | +30 |

### Deliberately out of scope

- Background polling / autonomous entity loops (entities still only act when prompted)
- Spawn/kill actions (only `message` type in Sprint 9)
- Cross-org messaging (blocked by existing `can_message()` permissions)
- Queue size limits or TTL (low volume, user-triggered)
- Code-fence false positives (`<hive_actions>` inside a code block would still
  be extracted — mitigated by the system prompt instructing entities to place
  it at the end)

### Verification

1. `ruff check src/ tests/` → clean.
2. `ruff format --check src/ tests/` → clean.
3. `pytest -v` → 256 passing (was 233; +23 new tests).

---

## Sprint 10 — Auto-Management (2026-04-17, DONE)

**Goal**: Make Hive self-managing with three features: auto-compact context,
auto-kill idle entities, and scheduled daily summaries.

**Builds on**: All prior sprints (token tracking, entity lifecycle, Telegram bridge)

### Why now

Until Sprint 10, every entity required manual management — context grew until
the user ran `/compact`, idle agents lingered forever, and there was no daily
activity report. These features eliminate babysitting so the user can focus on
directing work, not managing infrastructure.

### Phases (all complete)

**Phase 1 — Config.** Seven new env vars in `config.py`:
`HIVE_AUTO_COMPACT_ENABLED`, `HIVE_AUTO_COMPACT_THRESHOLD` (default 50000 tokens),
`HIVE_AUTO_KILL_IDLE_ENABLED`, `HIVE_IDLE_TIMEOUT_MINUTES` (default 30),
`HIVE_DAILY_SUMMARY_ENABLED`, `HIVE_DAILY_SUMMARY_HOUR` (default 23 UTC = 9am AEST),
`HIVE_SUMMARY_CHAT_ID`. All features enabled by default; summary requires
chat ID. `.env.example` updated.

**Phase 2 — Data model.** `last_activity_at: datetime | None` field on Entity.
Migration `010_last_activity_at.sql` adds the column. `EntityStore.upsert()`
and `_row_to_entity()` updated to persist/restore it.

**Phase 3 — Compact refactor.** Extracted compact logic from
`TelegramBridge._execute_compact()` into `ProcessManager.compact_entity()`.
The method: sends "summarize" prompt → kills entity → re-registers in IDLE →
seeds new session with summary → persists + audits. Bridge becomes a thin
wrapper. Also added notification callback infrastructure:
`set_notification_callback()`, `_notify()`, and a `_compacting: set[str]`
recursion guard.

**Phase 4 — Auto-compact.** After `_record_usage()` in `send_to_entity()`,
checks `session.last_usage["input_tokens"]` against `AUTO_COMPACT_THRESHOLD`.
If exceeded and not already compacting, calls `compact_entity()` and sends a
Telegram notification. Recursion guard prevents infinite loops (the compact
summarize call itself won't trigger another compact).

**Phase 5 — Auto-kill idle.** `send_to_entity()` now sets
`entity.last_activity_at = datetime.now(UTC)` on every call.
`kill_idle_entities(timeout_minutes, exempt_names)` loops all entities and
kills those past the cutoff. Default maestro is exempt. Background task
`idle_checker()` runs every 5 minutes via `asyncio.wait_for(stop_event.wait(),
timeout=300)` — exits promptly on shutdown.

**Phase 6 — Daily summary.** `TelegramBridge.format_daily_summary()` queries
all stores (entity statuses, completed tasks, token totals, error audit events)
for the last 24h and returns a Markdown summary. Background task
`daily_summary_scheduler()` checks hourly and sends at the configured UTC hour
via `_send_notification()`.

**Phase 7 — Integration.** Background tasks wired in `__main__.py` with
proper cleanup on shutdown (`task.cancel()` for each). Both tasks gated on
their respective `_ENABLED` env vars.

### Architecture decisions

- **Notification callback, not direct bridge dependency**: ProcessManager
  accepts a `Callable[[str], Awaitable[None]]` callback. TelegramBridge
  registers one in `start()`. This keeps the dependency one-way (bridge →
  manager) and avoids circular imports.

- **`asyncio.wait_for` over `asyncio.sleep`**: Background tasks use
  `await asyncio.wait_for(stop_event.wait(), timeout=N)` instead of
  `asyncio.sleep(N)`. This means they exit immediately on shutdown signals
  rather than blocking for up to N seconds.

- **Recursion guard for auto-compact**: The `_compacting: set[str]` prevents
  infinite loops where the compact's "summarize" call exceeds the threshold
  and triggers another compact.

### Critical files

**Created**: `src/hive/bus/migrations/010_last_activity_at.sql`,
`tests/test_auto_management.py`.

**Edited**: `src/hive/config.py`, `src/hive/models/entity.py`,
`src/hive/bus/entity_store.py`, `src/hive/process/manager.py`,
`src/hive/telegram/bridge.py`, `src/hive/__main__.py`, `.env.example`,
`tests/test_entity_store.py`, `tests/test_process_manager.py`.

### Verification

1. `ruff check src/ tests/` → clean.
2. `ruff format --check src/ tests/` → clean.
3. `pytest -v` → 275 passing (was 256; +19 new tests).

---

## Sprint 11 — Semantic Blueprints (2026-04-18, DONE)

**Goal**: Replace file-based blueprint text search with PostgreSQL + pgvector +
OpenAI embeddings, and auto-retrieve top-K relevant blueprints into every agent
prompt.

**Builds on**: Sprint 7 (file-based blueprints), Sprint 10 (config patterns,
auto-management background tasks)

### Why now

The file-based text search missed conceptually-related results — `/blueprint
search "auth"` wouldn't find a blueprint titled "JWT refresh logic" or "OAuth
redirect fix." Semantic similarity over embeddings makes institutional knowledge
retrievable by meaning, not keyword. Auto-retrieval means agents draw on past
work without the user having to quote blueprint IDs.

### Phases (all complete)

**Phase 1 — Image + deps.** Switched `docker-compose.yml` and testcontainer
image from `postgres:16-alpine` to `pgvector/pgvector:pg16` (drop-in
replacement that bundles the `vector` extension). Added `openai>=1.30` and
`pgvector>=0.3` runtime deps in `pyproject.toml`. The `pgvector` Python package
provides an asyncpg codec so `list[float]` ↔ `vector` conversion is automatic.

**Phase 2 — Migration 011.** `011_blueprints_pgvector.sql` enables `CREATE
EXTENSION vector`, creates the `blueprints` table with columns `id`, `title`,
`body`, `tags` (text[]), `embedding vector(1536)`, `created_at`. Adds HNSW
index on `embedding vector_cosine_ops` (cosine distance, pgvector's `<=>`
operator) plus a `created_at DESC` btree index for `list_all`.

**Phase 3 — Config.** Five new env vars in `config.py`: `OPENAI_API_KEY`
(required for blueprint features to work; Hive boots without it but `/blueprint`
commands and auto-retrieve become no-ops), `EMBEDDING_MODEL` (default
`text-embedding-3-small`), `EMBEDDING_DIM` (default 1536),
`AUTO_RETRIEVE_ENABLED` (default true), `AUTO_RETRIEVE_TOP_K` (default 3).

**Phase 4 — Embedder.** `src/hive/knowledge/embedder.py` — thin async wrapper
around `AsyncOpenAI.embeddings.create`. Lazy singleton client (`_get_client()`),
`embed_texts(list[str]) -> list[list[float]]`. Short-circuits on empty input.

**Phase 5 — Async BlueprintStore rewrite.** Replaced the file-based
`BlueprintStore` (Sprint 7) with an asyncpg-backed class. Three methods:
`save(title, body, tags) -> int` (embeds body, inserts row), `search(query,
limit) -> list[dict]` (embeds query, SELECT ordered by `embedding <=> $1` with
`id ASC` tiebreaker for deterministic ordering under duplicate distances),
`list_all() -> list[dict]` (newest first, no embeddings). Each call uses
`pgvector.asyncpg.register_vector(conn)` to register the connection-scoped
codec.

**Phase 6 — Telegram bridge async.** `_execute_blueprint` converted from sync
to async; caller at command dispatch switched to `await`. Uses existing
`_strip_quotes()` helper to handle titles with quotes safely.

**Phase 7 — Auto-retrieval hook.** `ProcessManager.__init__` gained
`blueprint_store` kwarg. In `send_to_entity`, after pending-message injection
and before CLI args build, an auto-retrieve step: if `AUTO_RETRIEVE_ENABLED` and
a store is configured, embed the prompt, fetch top-K blueprints, prepend them
under a "Relevant past blueprints" header separated by `---`. Exceptions are
logged but don't block the prompt.

**Phase 8 — Migration script.** `scripts/migrate_markdown_blueprints.py` —
one-off importer. Reads `*.md` files under `BLUEPRINTS_DIR`, parses YAML
frontmatter (`title`, `tags`), idempotent via existing-title check.

**Phase 9 — Wiring.** `__main__.py` constructs `BlueprintStore(store.pool)` and
passes it to both `ProcessManager` and the Telegram bridge.

### Architecture decisions

- **Single-provider commit (OpenAI `text-embedding-3-small`, 1536 dims, no
  abstraction layer).** Vectors from different providers live in incompatible
  spaces — not mechanically convertible. A swap would mean dropping the column
  and re-embedding every row from the canonical `body` text. Dual-indexing
  doubles cost for A/B gains we don't need. OpenAI's key also unlocks
  GPT/vision/Whisper for future side projects. Abstraction can be retrofitted
  later if a swap is ever wanted.

- **Cosine distance via `<=>` with HNSW index.** HNSW (Hierarchical Navigable
  Small World) is pgvector's fastest approximate-NN index type; cosine is the
  industry default for semantic text similarity.

- **Body column as source of truth; embedding is derived.** If we ever swap
  providers, we regenerate embeddings from `body`. The embedding is never the
  canonical representation.

- **Per-connection codec registration.** `register_vector` is connection-scoped
  — asyncpg pool connections are reused but codecs aren't inherited, so each
  `pool.acquire()` re-registers. Cheap and safe.

- **Graceful degradation without `OPENAI_API_KEY`.** Hive boots fine without the
  key, but `/blueprint save|search` and auto-retrieve silently become no-ops. No
  hard startup failure — deliberately low-friction for operators who don't need
  blueprints yet.

### Critical files

**Created**: `src/hive/bus/migrations/011_blueprints_pgvector.sql`,
`src/hive/knowledge/embedder.py`, `scripts/migrate_markdown_blueprints.py`,
`tests/knowledge/__init__.py`, `tests/knowledge/test_embedder.py`,
`tests/knowledge/test_blueprints_pgvector.py`, `tests/process/test_auto_retrieve.py`,
`tests/process/__init__.py` (if missing).

**Edited**: `src/hive/knowledge/blueprints.py` (full rewrite), `src/hive/config.py`,
`src/hive/process/manager.py`, `src/hive/telegram/bridge.py`,
`src/hive/__main__.py`, `pyproject.toml`, `docker-compose.yml`,
`tests/conftest.py`.

**Deleted**: `tests/test_blueprints.py` (9 obsolete tests against old
file-based API).

### Verification

1. `.venv/bin/ruff check src/ tests/` → clean.
2. `.venv/bin/ruff format --check src/ tests/` → clean.
3. `.venv/bin/pytest -v` → 275 passing (same count as Sprint 10: −9 obsolete
   file-based blueprint tests + 4 pgvector tests + 3 embedder tests + 2
   auto-retrieve tests = net 0).

---

## Sprint 3 — Multi-Team, Agent Lifecycle, Modes, Priorities

**Goal**: A maestro can manage multiple teams, each with a lead + workers. Full lifecycle with mode/loop switching and priority system.

**Builds on**: Sprint 1 (entity model, process manager), Sprint 2 (task queue, state persistence)

### New capabilities
1. **Multi-team under one maestro**
   - Maestro creates teams dynamically
   - Each team has a Lead who spawns/kills workers
   - Team-scoped context/memory

2. **Full agent lifecycle**
   - Ephemeral agents: spawn → task → report → die
   - Persistent leads: stay alive, manage their team
   - Auto-compact when context gets heavy
   - Auto-kill idle agents after timeout

3. **Mode switching** (permission levels)
   - `/mode plan|edit|auto [target]`
   - Maps to `claude -p` flags: `--permission-mode plan|default|bypassPermissions`

4. **Loop switching** (workflow patterns)
   - `/loop ralph|yolo|plan-act-observe|build-test-refine [target]`
   - Injected into entity's system prompt dynamically

5. **Priority system**
   - P0 (urgent) → P4 (backlog)
   - `/priority P0 "fix prod bug"` — P0 can pause lower-priority agents
   - Maestro re-evaluates priorities every N hours

### New/changed modules
| Module | Change |
|--------|--------|
| `models/team.py` | New — Team model (lead + workers, shared context) |
| `models/entity.py` | Extended — lifecycle hooks, auto-compact, timeout |
| `process/manager.py` | Extended — team-aware spawning, priority-based scheduling |
| `orchestrator/modes.py` | New — mode/loop management, system prompt injection |
| `orchestrator/priorities.py` | New — priority queue, resource allocation |
| `telegram/commands.py` | Extended — /mode, /loop, /priority, /teams, /swarm, /focus |

### Permission hierarchy (enforced)
- **You**: create/kill anything
- **Maestro**: create/kill teams + agents in own org. Can SUGGEST new maestro (needs your approval)
- **Lead**: create/kill workers in own team. Can suggest new teams (maestro decides)
- **Worker**: cannot create/kill. Can request help (lead decides)

### Verification
- `/new team backend` creates a team under dev maestro with a lead
- Lead spawns workers for subtasks, kills them when done
- `/mode plan dev` switches maestro to plan mode
- `/priority P0 "fix bug"` bumps task to top, pauses low-priority agents
- `/swarm backend` puts all backend workers on one task

---

## Sprint 4 — Multi-Maestro, Inter-Agent Comms, Personalities

**Goal**: Multiple maestros (dev, PA, etc.), cross-maestro communication, rich personality system.

**Builds on**: Sprint 3 (multi-team, lifecycle, priorities)

### New capabilities
1. **Multiple maestros**
   - `/new maestro pa "Personal assistant"` — creates a new maestro with personality
   - Each maestro = independent org with own teams
   - You can talk to any maestro: `/m:dev`, `/m:pa`
   - Maestros can suggest creating new maestros (you approve)

2. **Inter-agent messaging**
   - Any entity can message any other: cross-team, cross-maestro
   - Message bus extended with routing rules (who can talk to whom)
   - PA can ask Dev to build something; Dev delegates to team

3. **Rich personality system**
   - Personality .md files define: name, role, system prompt, tone, decision-making style
   - Hot-reload: edit personality file → takes effect on next interaction
   - Personality inheritance: team members inherit some traits from their lead/maestro

### New/changed modules
| Module | Change |
|--------|--------|
| `models/maestro.py` | Extended — org management, cross-maestro awareness |
| `bus/router.py` | Extended — cross-maestro routing, permission checks |
| `personalities/` | Extended — multiple personality files, inheritance |
| `orchestrator/comms.py` | New — inter-agent communication protocols |
| `telegram/commands.py` | Extended — /maestros, /new maestro, /broadcast |

### Verification
- Two maestros running simultaneously (dev + pa)
- PA sends task to dev via inter-maestro comms
- `/org` shows full hierarchy across all maestros
- `/broadcast "standup time"` reaches all entities

---

## Sprint 5 — Multi-LLM Routing, Batch API

**Goal**: Route tasks to the best model for the job. Use batch API for cost savings.

**Builds on**: Sprint 2 (token tracking, cost calculation), Sprint 3 (priority system)

### New capabilities
1. **Model routing**
   - Default: Claude via subscription (maximize Max plan value)
   - Fallback: API (Opus → Sonnet → Haiku based on task complexity)
   - Can also route to: OpenAI, Gemini, Ollama/local models
   - Cost-aware: subscription first, API when necessary

2. **Batch API for scheduled tasks**
   - Non-urgent tasks (P3/P4) batched for 50% cost savings
   - Batch results processed asynchronously

3. **Prompt caching**
   - Shared system prompts across agents (10% cost on cache hits)
   - Track cache hit rates

### New/changed modules
| Module | Change |
|--------|--------|
| `routing/model_router.py` | New — model selection logic |
| `routing/providers.py` | New — Claude API, OpenAI, Gemini, Ollama wrappers |
| `routing/batch.py` | New — batch API integration |
| `process/claude_session.py` | Extended — `--model` dynamic selection, `--fallback-model` |
| `tracking/costs.py` | Extended — multi-model cost tracking |

### Dependencies added
- `anthropic` (Claude API for batch/overflow)
- `openai` (optional, for OpenAI routing)
- `httpx` (for Ollama/generic HTTP LLM endpoints)

### Verification
- Simple task routes to Haiku, complex to Opus
- `/cost` shows per-model breakdown
- Batch tasks processed with 50% cost savings visible in logs

---

## Sprint 6 — Payment Lead / Vault

**Goal**: Isolated, security-critical agent for financial operations.

**Builds on**: Sprint 4 (multi-maestro, personalities), Sprint 2 (audit log)

### Security design (CRITICAL)
- **Never acts autonomously** — every action requires your approval
- **NO sudo, NO bash, NO file access** — only payment APIs + audit log
- **Cannot be killed by maestro** — only by you
- **Double confirmation** above configurable threshold
- **Full audit log** — every action recorded with timestamp

### New/changed modules
| Module | Change |
|--------|--------|
| `models/vault.py` | New — Vault entity with locked-down permissions |
| `vault/apis.py` | New — payment API integrations (Stripe, etc.) |
| `vault/audit.py` | New — immutable audit log |
| `vault/approval.py` | New — approval flow via Telegram |
| `telegram/commands.py` | Extended — /vault status, /vault log, /vault approve, /vault deny, /vault limit |
| `process/manager.py` | Extended — Vault cannot be killed by maestro |

### Verification
- Vault entity running with zero bash/file tools
- `/vault status` shows it's alive
- Payment request → Telegram approval prompt → execute or deny
- `/vault log` shows full history
- Maestro cannot `/kill vault` — only you can

---

## Sprint 7 — Knowledge System (Semantic Caching, pgvector, Search)

**Goal**: Agents learn from past work. Semantic search over project blueprints.

**Builds on**: Sprint 2 (PostgreSQL), Sprint 4 (personalities), accumulated project data

### Progressive knowledge layers
1. **Blueprints** (already started in Sprint 1 as .md files)
   - After each project: auto-generate `blueprint.md`
   - Stored in `/projects/<name>/blueprints/` and `~/.hive/knowledge/`

2. **Semantic caching**
   - Store query-answer pairs in PostgreSQL
   - Before calling LLM, check for semantically similar past queries
   - 30-50% fewer LLM calls for repeated patterns

3. **pgvector search**
   - PostgreSQL + pgvector extension
   - Embed blueprints, store vectors, enable semantic search
   - Agents can query: "How did we solve auth last time?"

### New/changed modules
| Module | Change |
|--------|--------|
| `knowledge/blueprints.py` | New — blueprint generation and storage |
| `knowledge/cache.py` | New — semantic cache (embed → compare → hit/miss) |
| `knowledge/search.py` | New — pgvector semantic search |
| `knowledge/embeddings.py` | New — embedding API (Claude/OpenAI) |

### Dependencies added
- `pgvector` (PostgreSQL vector extension)
- Embedding API access (Claude or OpenAI embeddings)

### Database additions
```sql
-- pgvector extension
CREATE EXTENSION vector;
knowledge_chunks(id, source, content, embedding vector(1536), metadata, created_at)
query_cache(id, query_text, query_embedding vector(1536), response, hit_count, created_at)
```

### Verification
- Agent asks "how did we handle auth?" → gets relevant blueprint chunks
- Repeated similar query hits cache instead of LLM
- `/knowledge search "deployment"` returns relevant past work

---

## Sprint 8+ — Web App, Web CLI, Dashboard

**Goal**: Full web interface beyond Telegram.

**Builds on**: Everything

### Scope (high-level — detailed planning closer to sprint)
1. **Web dashboard**
   - Real-time org chart visualization
   - Token usage graphs, cost tracking
   - Agent status, message logs, task board
   - Built with: FastAPI backend + React frontend (or htmx for simplicity)

2. **Web CLI / Terminal**
   - Browser-based terminal with authenticated sudo (Tier 3 sudo access)
   - Same commands as Telegram

3. **Observability dashboard**
   - Grafana or custom: agent performance, error rates, token burn
   - Sprint retrospectives rendered as reports

### Tech choices (to decide closer to sprint)
- FastAPI + htmx (simpler, Python-only) vs FastAPI + React (richer UI)
- WebSocket for real-time updates
- OAuth/session auth for web access

---

## Cross-Sprint Concerns

### Observability & Reporting (starts Sprint 1, matures over time)
- **Sprint 1**: Basic `/status` and `/health` commands
- **Sprint 2**: Token tracking, `/cost` command
- **Sprint 3**: Daily summaries (9am AEST) + mid-day checkpoint via Telegram
- **Sprint 4**: Cross-maestro reporting
- **Sprint 8+**: Full web dashboard

### Error Recovery (starts Sprint 3)
- Agent fails → escalates to Lead
- Lead decides: retry, spawn helper, or rework approach
- Uses agent loops for recovery strategy selection
- Low-priority tasks auto-suspended when resources scarce

### Session Health (starts Sprint 1, enhanced Sprint 3)
- `/compact [target]` — compress context
- `/reset [target]` — kill session, preserve memory, restart fresh
- Auto-compact when context gets heavy (monitored by orchestrator)

### Security (every sprint)
- No agent gets persistent sudo
- Least privilege: minimum tools per entity
- Confirmation prompts for destructive actions
- Audit log for all actions (Sprint 2+)

### Smart Concurrency (Sprint 3+)
- Agent budget calculated per sprint based on tasks + token budget
- Maestro distributes slots to teams by priority + workload
- Mid-sprint rebalancing: burning fast → reduce agents, under budget → spin up more
- Reserve slot for P0 emergencies

### Sudo Access — 3 Tiers
- **Tier 1** (Sprint 3): Passwordless sudo whitelist via sudoers. Agent requests → you `/approve` on Telegram
- **Tier 2** (already works): SSH in via Tailscale for non-whitelisted commands
- **Tier 3** (Sprint 8+): Web app terminal with authenticated session

---

## Directory Structure (final state)

```
hive/
├── src/hive/
│   ├── __init__.py
│   ├── __main__.py
│   ├── config.py
│   ├── models/          # Entity, Maestro, TeamLead, Worker, Vault, Task, Team
│   ├── process/         # ClaudeSession, ProcessManager, WorktreeManager
│   ├── bus/             # MessageStore, MessageRouter, migrations/
│   ├── telegram/        # TelegramBridge, CommandParser
│   ├── cli/             # Local CLI
│   ├── orchestrator/    # Modes, Priorities, Comms
│   ├── routing/         # ModelRouter, Providers, BatchAPI
│   ├── vault/           # APIs, Audit, Approval
│   ├── knowledge/       # Blueprints, Cache, Search, Embeddings
│   └── tracking/        # Tokens, Costs
├── tests/               # mirrors src/ structure
├── personalities/       # .md files per entity
├── docs/                # PROJECT_PLAN.md, architecture docs
├── data/                # gitignored runtime data
└── worktrees/           # gitignored git worktrees
```

---

## Testing Strategy (all sprints)

- **Unit tests**: Fast, no subprocesses — state machines, parsing, CRUD, routing logic
- **Integration tests** (`@pytest.mark.integration`): Real `claude -p` spawn, real DB — skipped in CI
- **Manual tests**: Telegram round-trip, multi-entity flows
- **CI**: ruff + pytest on every push/PR
