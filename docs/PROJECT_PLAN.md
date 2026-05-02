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
`tests/knowledge/test_blueprints_pgvector.py`,
`tests/process/test_auto_retrieve.py`.

**Edited**: `src/hive/knowledge/blueprints.py` (full rewrite), `src/hive/config.py`,
`src/hive/process/manager.py`, `src/hive/telegram/bridge.py`,
`src/hive/__main__.py`, `pyproject.toml`, `docker-compose.yml`,
`tests/conftest.py`.

**Deleted**: `tests/test_blueprints.py` (9 obsolete tests against old
file-based API).

### Verification

1. `ruff check src/ tests/` → clean.
2. `ruff format --check src/ tests/` → clean.
3. `pytest -v` → 275 passing (same count as Sprint 10: −9 obsolete file-based
   blueprint tests + 4 pgvector tests + 3 embedder tests + 2 auto-retrieve
   tests = net 0).
4. **VPS deployed 2026-04-18 13:32 UTC.** Migration 11 applied against
   `pgvector/pgvector:pg16` (pgvector 0.8.2). Telegram smoke test:
   `/blueprint save` stored blueprint #1, `/blueprint search rollout`
   returned the saved "deployed" blueprint at cosine distance 0.577
   (conceptual match — substring search would have missed it), and
   `/m:dev what do we know about recent rollouts?` replied by directly
   referencing the saved blueprint, proving auto-retrieval fired.

### Post-ship notes

- **Auto-retrieval scope**: verified to apply to every entity type
  (maestro, team lead, worker). `ProcessManager.send_to_entity()`
  (`src/hive/process/manager.py:282-294`) has no role gating, and every
  prompt dispatch path — Telegram `/m:`, `/t:`, `/a:`, `/swarm`,
  `/broadcast`, local CLI, and autonomous inter-entity messages drained
  from the router queue — funnels through that single method.

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

> **Status note**: Partially superseded by the actual Sprint 7 (file-based
> blueprints, 2026-04-16, DONE) and Sprint 11 (semantic blueprints via
> pgvector, 2026-04-18, DONE). The blueprint+pgvector pieces are shipped;
> semantic query caching and a generic `/knowledge` search are still
> unbuilt.

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

## Sprint 22 — Identity & Role JDs (2026-05-02, Phase 1 + 1.5 + 2 + 3 DONE)

**Status:** All four phases complete on the worktree branch
`feat-entity-identity`; awaiting deploy + live verification.
**Branch:** `feat-entity-identity` → main
**Totals:** 551 → 638 tests (+87). No migrations.

**Goal**: fix the `dev.mdcount` bug where leads hallucinated worker
names (emitting `{"lead": "maestro", ...}` because the prompt told them
the literal placeholder `<full.lead.name>` and they had no other name
to substitute), and lay the scaffolding for autonomous, well-named
personalities.

### Locked decisions (Phase 1)
- **Identity preamble first**: every entity, every role, gets an
  identity block as the *first* `--append-system-prompt`:
  `You are <name>. Your role is <role>.` Maestros and leads also get a
  placeholder-substitution warning ("use `<self.name>` wherever the
  guidance shows `<full.lead.name>`"). All roles get a closing honesty
  clause. Workers don't need the placeholder warning so they don't get
  one — keeps `role-worker.md` from including spawn-action vocabulary.
- **Role JDs are markdown, not constants**: `MESSAGING_PROMPT` and
  `AUTONOMY_PROMPT` deleted from `src/hive/process/loops.py`. Replaced
  by `load_role_jd(role)` which reads from
  `personalities/role-<role>.md` and caches per-process via
  `lru_cache`. Three role files committed: `role-maestro.md`,
  `role-lead.md`, `role-worker.md`.
- **Per-entity personality file naming**: `personalities/dev.md`
  (matches dotted name) replaces `personalities/maestro-dev.md`. The
  `maestro-` prefix added nothing — leads will be `personalities/dev.backend.md`
  not `personalities/lead-dev.backend.md`.
- **System Prompt section is personality-only**: role JD is loaded
  separately, so `personalities/dev.md`'s System Prompt now describes
  *who Dev is* (direct, opinionated, web-focused) not *what a maestro
  does*. No duplication across maestros.

### Files changed (Phase 1)

| File | Change |
|------|--------|
| `src/hive/models/entity.py` | `build_cli_args()` — identity preamble emitted first; role JD loaded via `load_role_jd(self.role)` instead of two static constants. |
| `src/hive/process/loops.py` | `MESSAGING_PROMPT` and `AUTONOMY_PROMPT` removed. New `load_role_jd(role, base_dir=None)` with `lru_cache`-backed reader; resolves `personalities/role-<role>.md` relative to repo root by default. |
| `src/hive/__main__.py` | Default-maestro path looks for `personalities/dev.md` instead of `personalities/maestro-dev.md`. |
| `personalities/role-maestro.md` (new) | Maestro JD: project management, team formation with `display_name`/`personality`, delegation, reporting; `spawn_team`/`spawn_worker`/`kill_entity` action vocabulary; honesty clause. |
| `personalities/role-lead.md` (new) | Lead JD: scope ownership, worker formation, delegation; `spawn_worker` (under self only) + `kill_entity` (own workers only); explicit instruction to substitute own dotted name where `<full.lead.name>` appears. |
| `personalities/role-worker.md` (new) | Worker JD: focused subtask, report back, stay in scope; messaging-only protocol; honesty clause. Deliberately omits spawn vocabulary. |
| `personalities/dev.md` (renamed from `maestro-dev.md`) | System Prompt slimmed to personality (Dev voice). Role JD now comes from `role-maestro.md`. |
| `tests/test_entity.py` | `TestMessagingPromptInjection` replaced — counts now 3 appended prompts for every role (identity + loop + role JD). New `TestIdentityPreamble` (4 tests) verifying preamble is first and contains the entity's name. |
| `tests/test_role_jd.py` (new) | 10 tests — happy-path loads, unknown-role rejection, missing-file error, caching behaviour, repo-level role files exist with expected vocabulary. |

### Verification (Phase 1)
- `pytest -m "not integration" -q` → **602 passing** (was 551).
- Path resolution under editable install: `_DEFAULT_BASE_DIR` resolves
  to `/.../personalities` correctly; `load_role_jd("maestro")` returns
  the maestro JD.
- Live demo (after `git push` + `systemctl --user restart hive.service`):
  `/t:dev.mdcount please spawn your backend and qa workers now` —
  expect journal to show `dev.mdcount.backend` and `dev.mdcount.qa`
  registered, no `denied: dev.mdcount -> maestro`.

### Phase 1.5 — Protocol fix: drop required `lead` field (2026-05-02)

**Why this exists.** Phase 1's identity preamble plus the
placeholder-substitution warning *should* have been enough to fix the
`{"lead": "maestro"}` bug. It wasn't. Live demo (`/t:dev.smoke …`)
showed leads now emit `{"lead": "lead"}` — they pattern-match on the
JSON field name **`lead`** itself, so whatever the prompt says about
substitution gets overridden by the LLM picking the strongest nearby
token (the field name).

**The fix.** Stop asking the lead to repeat a value the orchestrator
already knows. `spawn_team` already does this right — the maestro
doesn't include its own name, the orchestrator infers it. Apply the
same pattern to `spawn_worker`:

- `lead` is now **optional** in the action protocol.
- When omitted, the manager fills it from the actor: a lead spawns
  under itself; a maestro is rejected (the orchestrator can't guess
  which team the maestro means).
- `role-lead.md` updated to explicitly tell leads **not** to include
  the `lead` field.
- `personalities/role-lead.md`'s spawn-action schema removes the
  `<full.lead.name>` placeholder. Identity preamble drops the
  placeholder-substitution warning (no placeholder to substitute).

**Files changed (Phase 1.5)**

| File | Change |
|------|--------|
| `src/hive/bus/actions.py` | `_SPAWN_WORKER_REQUIRED` now empty; parser accepts `spawn_worker` with no `lead` field. Module docstring updated. |
| `src/hive/process/manager.py` | Dispatch fills `action.lead = entity.name` for leads when missing; rejects + audits `entity.spawn_worker_denied{reason="missing_lead"}` for maestros that omit it. |
| `src/hive/models/entity.py` | Identity preamble simplified — drops placeholder-substitution warning since `role-lead.md` no longer has one. |
| `personalities/role-lead.md` | spawn_worker schema documents no `lead` field; explicit "do not include `lead`" instruction. |
| `tests/test_actions.py` | Replaced `test_spawn_worker_missing_lead_skipped` with two tests asserting `lead` is optional and parses to `None`. |
| `tests/test_process_manager.py` | Two new dispatch tests: lead-omits-lead spawns under self; maestro-omits-lead is denied + audited with reason `missing_lead`. |

**Verification (Phase 1.5)**
- `pytest -m "not integration" -q` → **605 passing** (+2).
- Live demo (after deploy): `/t:dev.smoke please spawn your backend and
  qa workers now` should now register `dev.smoke.backend` and
  `dev.smoke.qa` (no more `denied: ... -> lead`).

### Phase 2 — Interactive `/new maestro` flow (2026-05-02)

**Why this exists.** Before this change, `/new maestro <name>` failed
loudly when the personality file was missing — users had to hand-author
markdown before registering. Phase 2 turns the missing-file branch
into a guided multi-turn Q&A: the dispatcher asks for purpose and
communication style, then renders a templated `personalities/<name>.md`
and registers the maestro.

**Scope decision: templated, not LLM-authored (yet).** The original plan
called for a Claude call ("personality-author" system prompt) to
elaborate the user's answers into the markdown. We shipped the
deterministic templated version instead: it's fully testable, ships
faster, and the markdown structure already mirrors `_template.md` so
`parse_personality` reads it. **LLM-authored generation is deferred to
Phase 2.5** and can be added by replacing `_render_personality_md`
with a Claude call without touching the conversation-state machine.

**Locked decisions (Phase 2)**
- **In-memory state on the dispatcher**, keyed by `actor`. No separate
  module — it's a simple `dict[str, _PendingNewMaestro]` on the
  CommandDispatcher instance. Per-actor isolation lets multiple users
  run flows concurrently. (Single-process service, single-user Max
  plan — survives restart isn't required for v1.)
- **Hooked in `dispatch()` only** — the single chokepoint between
  parsed-text surfaces (web + telegram) and `dispatch_command()`. No
  changes needed to either bridge: both already go through `dispatch()`.
- **Slash commands cancel the flow.** A pending flow + `/cancel` aborts
  with no side effects; any other slash command cancels and then
  executes the new command. Plain text is interpreted as the next
  answer.
- **10-minute inactivity timeout** so abandoned flows don't trap an
  actor's plain text. Expiry is checked on each dispatch, not by a
  background reaper.
- **File model overrides CLI model.** When a personality file already
  exists, its `**Model**` field is authoritative and the CLI arg is
  ignored — matches the existing `/personality reload` semantics.
  When the file doesn't exist (flow path), the CLI arg is the seed for
  the freshly-written file.

**Files changed (Phase 2)**

| File | Change |
|------|--------|
| `src/hive/commands/dispatch.py` | `CommandDispatcher.__init__` accepts `personalities_dir` (default `Path("personalities")`). New `_pending_new` dict + `_PendingNewMaestro` dataclass + `_NEW_MAESTRO_QUESTIONS`. `dispatch()` intercepts plain text from actors with active pending state. `_execute_new` starts the flow when no file exists. New `_advance_new_flow`, `_finalize_new_maestro`, module-level `_render_personality_md`. |
| `tests/test_command_dispatcher.py` | Existing /new tests adapted to pre-create personality files (file-exists branch). New `TestNewMaestroInteractiveFlow` (9 tests) covers first-question, plain-text-advances, full-flow-writes-and-registers, explicit-model-via-flow, /cancel, other-command-cancels, per-actor isolation, timeout, and graceful name-conflict. |

**Verification (Phase 2)**
- `pytest -q` → **615 passing** (+10 over Phase 1.5).
- Live: `/new maestro pa` (no `personalities/pa.md`) → walks the user
  through both questions, writes `personalities/pa.md`, registers `pa`.
  `/api/org` shows the new maestro.

### Phase 3 — Autonomous personality generation + cleanup-on-kill (2026-05-02)

**Why this exists.** Phases 1–2 made identity reliable for human-named
entities. Phase 3 closes the loop for entities the orchestrator spawns
autonomously: the parent (maestro for teams, lead for workers) supplies
a `display_name` + `personality` blurb in its `<hive_actions>` block,
and the `ProcessManager` writes a personality file *and immediately
loads it into the freshly-spawned entity*, so the very next CLI args
include the personality `--system-prompt` (no manual reload needed).
Files written by the system carry an `auto_generated: true` YAML
frontmatter so `kill_entity` knows it can delete them on cleanup.
User-authored files (no frontmatter) are always preserved.

**Locked decisions (Phase 3)**
- **Pair-or-nothing.** Both `display_name` AND `personality` must be
  present for a file to be written; missing either is treated the same
  as missing both (the entity still spawns, just without a generated
  personality file). Rationale: writing a stub with only one of the
  two yields a half-formed identity that's worse than no file. Diverges
  from the original plan ("write a minimal stub when fields missing")
  — locked to keep disk clean and behaviour predictable.
- **Don't-clobber.** If a file already exists at the target path, the
  spawn never overwrites it. First parent wins, and any user-authored
  file at that path is permanently safe. Verified by a dedicated test
  that pre-writes a user-authored file and asserts both spawn and kill
  leave it untouched.
- **Frontmatter format**: `---\nauto_generated: true\n---\n` at the
  top of file, then standard `_template.md`-style sections. The
  existing `parse_personality` regex matches `## ` section headers
  and is unaffected by leading frontmatter (verified by test).
- **Helper, not extension.** `is_auto_generated_personality(path)` is
  a separate pure function in `entity.py`; `PersonalityConfig` does
  not gain an `auto_generated` field because only `kill_entity` cares.
- **Dispatch loop extraction.** The action-routing tail of
  `send_to_entity` extracted into `_handle_actions(entity_name,
  clean_text, actions)`, so lifecycle tests can drive the full
  parser→dispatch→manager path without spawning a Claude subprocess.
- **Load-after-write.** When `_maybe_write_auto_personality` writes a
  file, `create_team`/`spawn_worker` set `entity.personality_path` to
  the written path and call `entity.load_personality()` before
  `_persist`, so the persisted row reflects the loaded state and the
  next `build_cli_args()` injects the `--system-prompt` flag. Without
  this, the file would sit on disk inert until a manual
  `/personality reload` and the role JD's "identity from birth"
  guarantee would be false.

**Files changed (Phase 3)**

| File | Change |
|------|--------|
| `src/hive/bus/actions.py` | `Action` gains `display_name` and `personality` (both `str | None`). Parser populates them on `spawn_team` and `spawn_worker`. |
| `src/hive/models/entity.py` | New `is_auto_generated_personality(path)` helper — returns True iff the file's first frontmatter block contains `auto_generated: true`. |
| `src/hive/process/manager.py` | `__init__` accepts `personalities_dir` (default `Path("personalities")`). New `_render_auto_personality` (module-level), `_personality_path`, `_maybe_write_auto_personality` (returns the written `Path` or `None`), `_maybe_delete_auto_personality`. `create_team` and `spawn_worker` accept and forward the new fields, set `entity.personality_path` and call `entity.load_personality()` when a file was written, and persist after the load. `kill_entity` deletes the file iff auto-generated. Action dispatch loop extracted into `_handle_actions(entity_name, clean_text, actions: list[Action])` and forwards `action.display_name`/`action.personality`. |
| `personalities/role-maestro.md` | Added a fully worked `spawn_team` example with concrete `display_name`/`personality` values and an explicit pair-or-nothing note. |
| `personalities/role-lead.md` | Same for `spawn_worker`. |
| `tests/test_actions.py` | Four new parser tests across `spawn_team` and `spawn_worker`. |
| `tests/test_personality_lifecycle.py` (new) | 19 tests covering the helper, frontmatter compatibility with `parse_personality`, write-on-spawn (pair-or-nothing + don't-clobber), in-memory load-after-write for both `create_team` and `spawn_worker` (and the no-load case when the pair is incomplete), kill cleanup, cascading kill via `kill_team`, and wire-through from parsed action to manager call (file write **and** in-memory load). |

**Verification (Phase 3)**
- `pytest -q` → **638 passing** (+23 over Phase 2).
- Live: kill `dev.mdcount` (no auto file). Send Dev a fresh task;
  Dev's reply emits `spawn_team` with `display_name`/`personality`;
  confirm `personalities/dev.<team>.md` written with frontmatter.
  Then kill the lead and confirm the file is removed. Spawn another
  team and pre-write a user file at the target path; confirm both
  the spawn and a subsequent kill leave it intact.

### Out of scope (deferred)
- **Phase 2.5**: replace `_render_personality_md` with a Claude call so
  the generated MD is richer than the deterministic template. Same
  hook applies to Phase 3's `_render_auto_personality` — both can be
  upgraded later by swapping the renderer without touching action
  parsing or the manager's lifecycle bookkeeping.

---

## Sprint 21 — Restart Persistence (planned)

**Status:** Planned
**Branch:** `sprint-21-restart-persistence` (not yet cut)
**Builds on:** Sprint 19 (autonomous spawn/kill, entity store), Sprint 20 (dashboard read paths).

**Goal**: every entity that exists before `systemctl restart hive.service` comes back after restart with the same role, model, hierarchy, and session id, in IDLE state ready to accept the next message. `/kill foo` continues to hard-delete (intentional user action); shutdown does not.

### Why

The plumbing is 90% there:

- `entity_store.all()` already restores entities on boot (`__main__.py:204-209`).
- `process_manager.rebuild_hierarchy()` reconnects maestro → lead → worker links (`manager.py:1169-1206`).
- `entity.session_id` already persists, so multi-turn Claude conversations resume on the next message via `claude --resume <session_id>`.
- All other tabs already persist (DB-backed): Tasks, Audit, Vault, Blueprints, Attachments, Mode requests, Token usage, Dashboard widgets (derived from those tables).

The break: `__main__.py:347` calls `process_manager.kill_all()` on shutdown, which calls `kill_entity()` per entity, which deletes the row from `entities` (`manager.py:771`). So every restart wipes the registry; only `dev` re-appears because `__main__.py:213-220` re-registers it from scratch. This sprint splits "shutdown" from "kill" so the row is preserved across a restart.

### Phases (planned)

**Phase 1. Split shutdown from kill.** Add `process_manager.stop_all()` that terminates subprocesses and marks each entity `STOPPED` but does NOT delete from `entity_store`. `kill_entity()` keeps its current hard-delete semantics. `__main__.py:347` switches from `kill_all()` to `stop_all()`. Factor a private `_stop_subprocess(name)` helper so `stop_all` and `kill_entity` share the subprocess-teardown half but only `kill_entity` deletes the DB row.

**Phase 2. Boot path: idle, not running.** Restored entities come back as IDLE (`process_manager.restore` at `manager.py:1322` already does this). Subprocess re-spawn stays lazy — first message routed to the entity triggers `_send_to_entity` → `_ensure_session()` → `claude --resume <session_id>` using the persisted session id. No eager spawn; saves cost when a maestro is idle through a deploy.

  **Forward dependency from Sprint 22 Phase 3 — load personality on restore.** The auto-personality contract is *path-set ⇔ personality-loaded-in-memory* (the spawn path calls `entity.load_personality()` immediately after writing the file, so the next `build_cli_args()` injects `--system-prompt`). `restore()` today re-registers the entity but does not load. Add: when restoring an entity with a non-None `personality_path` that exists on disk, call `entity.load_personality()` before `register`. Without this, restarted auto-spawned leads will have the file on disk but an empty `system_prompt`, breaking "identity from birth" silently across restarts. Add a regression test: spawn lead with `display_name`+`personality`, simulate restart by re-instantiating manager from `entity_store.all()`, assert `system_prompt` non-empty on the restored lead.

**Phase 3. Edge cases.** Stale session id → fall back to fresh session, log a warning, audit `entity.session_reset`. Worker worktree gone but row exists → recreate on first message. Audit log: shutdown emits `entity.stop` (vs. today's `entity.kill`); restart emits `entity.restore`.

### Critical files

**Created**: none.
**Edited**:
- `src/hive/process/manager.py` — add `stop_all()` and `_stop_subprocess()`, refactor `kill_entity` to delegate.
- `src/hive/__main__.py` — line 347 `kill_all()` → `stop_all()`.
- `tests/test_process_manager.py` — restart-persistence integration tests (see below).

### Verification

1. `pytest tests/ -q` — all passing including new restart-persistence tests:
   - register dev + maestro `foo`. `stop_all()`. Confirm both rows still in DB. Re-instantiate the manager from `entity_store.all()`. Confirm both back as IDLE with their original models and session ids.
   - Full hierarchy: maestro + lead + worker. `stop_all()`, restore, `rebuild_hierarchy()`. Confirm hierarchy intact.
   - **Restored auto-personality is loaded into `system_prompt`** (Sprint 22 Phase 3 forward-dep). Spawn a lead with `display_name`+`personality`, `stop_all()`, restore from DB, assert restored lead's `system_prompt` is non-empty and contains the personality blurb.
   - Regression: `/kill foo` still deletes the row.
2. End-to-end: `/new maestro testbot` → `/status` shows `testbot, dev` → `systemctl --user restart hive.service` → `/status` still shows `testbot, dev` → send `testbot ping`, reply uses the resumed session → `/kill testbot` → restart → `/status` shows `dev` only.
3. Other tabs (Projects, Dashboard, Knowledge, Tasks, Vault) remain populated across the restart. These already persist; this sprint adds a regression test confirming so.

### Out of scope (deferred to Sprint 22+)

- In-flight messages in `router.MessageQueue` (volatile `asyncio.Queue`) — dropped on shutdown today; a restart-persistent message queue is its own sprint.
- Hot reattach of an actively-running Claude subprocess. The subprocess exits on stop; conversation context resumes via `--resume` on the next message. Live attach (no re-spawn) would need IPC + PID handoff and is not required for the user's "2 maestros come back as 2 maestros" goal.

---

## Sprint 20 — Dashboard Tab (2026-05-01, DONE)

**Status:** Complete
**Branch:** `sprint-20-dashboard-tab` → main
**Totals:** 551 → 583 tests (+32). No migrations.

**Goal**: fill the long-reserved **Dashboard** tab placeholder on the
landing's top bar with a dense observability surface for the agents
themselves — cost burn, token mix, cache efficiency, audit stream,
backlog, system health. Sprints 14–19 piled telemetry into Postgres
(`token_usage`, `audit_log`, `tasks`); the data was queryable via
`/cost`, `/audit`, `/tasks` Telegram commands but never collated into
one view. Sprint 20 ships that view.

The design is a handoff bundle from claude.ai/design: 8 widgets
(W1–W8), hand-rolled SVG, "Paper Ops" warm palette matching the
existing landing.

### Locked decisions

- **Render chrome from Jinja, widgets from React.** The design's
  `D_TopBar` and `D_TerminalBar` were dropped; the dashboard inherits
  the existing landing's chrome with the **Dashboard** tab active and
  the **Hive** tab linking back to `/`. Mounts React only into a
  single `#root` widget-grid container.
- **Babel-in-browser** (no build step). Three JSX files load via
  `<script type="text/babel" src="…">` from unpkg's React 18.3.1 +
  Babel-standalone 7.29.0. Trade-off: ~5MB of CDN payload on first
  load, no toolchain. Acceptable for an internal dashboard; if the
  payload becomes a problem later, switch to a Vite build.
- **First-paint = server-rendered `window.HIVE_DASH = {{ data |
  tojson }}`** inline (matches existing landing precedent).
- **Refresh = 30s polling** of `/api/dashboard/all`. `setInterval`
  reassigns `window.HIVE_DASH` and dispatches a `'hive-data-updated'`
  custom event; `DashboardPage` listens and bumps a `useState` tick to
  re-render. Skipped SSE — polling matches the landing's htmx pattern.
- **Wire 5 widgets to real telemetry, mock 3** with `# TODO Sprint 21+:`
  markers — health probes, CFD anomaly heuristics, and the failure
  classifier all need new instrumentation that's not in this sprint.
- **`/api/dashboard/all` is bearer-token protected** via the existing
  `Depends(require_token)`. Per-entity cost is more sensitive than the
  landing hero, which stays open behind the Tailscale bind.
- **Renamed `templates/dashboard.html` → `landing.html`** (the file
  already self-titles "Hive — Landing"). One-time cost; eliminates a
  permanent name collision and frees up the canonical name for the
  new dashboard route.

### Files changed

| File | Change |
|------|--------|
| `src/hive/bus/token_store.py` | +5 aggregation methods: `daily_cost(days)`, `token_burn(window, buckets)`, `cost_by_entity_model(since)`, `cache_stats(since)`, `cache_overall_daily(days)`. CTE-based bucket scheme for `token_burn`; zero-fill via `generate_series` for daily series. |
| `src/hive/bus/audit_log.py` | +`histogram(window_minutes=60)` — 60 buckets of 1 min, each `{i, command, entity, task, git}` from `split_part(action, '.', 1)`. |
| `src/hive/web/view_model.py` | +`build_dashboard_view_model()` shaping the `window.HIVE_DASH` payload + 8 helpers (`_build_cost30` per-DOW median/stdev, `_build_cfd` 42-point ramp, `_build_burn` 4 ranges, `_build_matrix`, `_build_cache`, `_build_histogram`, `_build_audit_feed`, `_entity_names`). Mock helpers `_mock_health` for the 3 deferred widgets. |
| `src/hive/web/app.py` | Renamed `dashboard()` → `landing()` (route stays at `/`); new `dashboard()` at `/dashboard`; new `GET /api/dashboard/all` token-protected. |
| `src/hive/web/templates/landing.html` (renamed) | Dashboard tab now `<a href="/dashboard">Dashboard</a>` instead of `<button class="tab" title="Coming soon">`. |
| `src/hive/web/templates/dashboard.html` (new) | Jinja chrome (top-bar mirrored from landing, no chat-rail), `window.HIVE_DASH` first-paint script, React 18.3.1 + Babel CDN loads, three JSX files, inline `DashboardPage` component wrapped in `{% raw %}…{% endraw %}` (JSX `{ }` clashes with Jinja `{{ }}`). |
| `src/hive/web/static/dashboard/dashboard-shell.jsx` (new, copied) | Carries the D palette, dStyles, atoms (D_Bee, D_Hex, D_StateDot, D_PriorityPill, D_NSPill, D_Card, D_PageHeader, D_Backdrop). |
| `src/hive/web/static/dashboard/dashboard-w1234.jsx` (new, copied) | W1 cost ribbon, W2 system health, W3 workload CFD, W4 token burn. |
| `src/hive/web/static/dashboard/dashboard-w5678.jsx` (new, copied + patched) | W5 entity×model cost matrix, W6 cache hit, W7 audit timeline, W8 failure scatter. Patch: W7 namespace array `['command','entity','task','vault','mcp']` → `['command','entity','task','git']` (matches what `audit_log.action` actually contains; `vault`/`mcp` were design assumptions, not real namespaces). |
| `src/hive/web/static/dashboard/refresh.js` (new) | 30s `setInterval` calling `/api/dashboard/all` with `Authorization: Bearer ${sessionStorage.getItem('hive_web_token')}`; respects `window.HIVE_AUTO_REFRESH === false` toggle; degrades silently if no token. |
| `src/hive/web/static/dashboard/dashboard.css` (new) | Minimal scaffolding: `.dashboard-main` (flex/overflow auto), `#root` min-height, `@keyframes d-bob` and `@keyframes d-blink` for the design's bee + sage dot animations. |
| `src/hive/web/templates/_macros.html` | Updated stale `dashboard.html` reference to `landing.html`. |
| `tests/test_token_store_dashboard.py` (new) | 16 tests — daily_cost (zero-fill, group, DOW, exclude outside window), token_burn (zero-fill, aggregates, exclude outside window), cost_by_entity_model (empty, groups, since-filter), cache_stats (empty, hit_pct, per-entity, exclude outside window), cache_overall_daily (zero-fill, today). |
| `tests/test_audit_log_histogram.py` (new) | 6 tests — bucket count, zero-fill, namespace counting, recent-event placement, old-event placement, exclusion outside window. |
| `tests/test_web_dashboard.py` (new) | 10 tests — `/dashboard` 200 + `id="root"` + `window.HIVE_DASH` + Hive tab links back to `/`; static JSX/refresh.js served; `/api/dashboard/all` requires token (401) + returns full payload (16 keys); view-model empty shape; JSON-serializable round-trip; CFD 42 points + dayBoundaries `[5,11,17,23,29,35,41]`. |

### Verification
- `pytest tests/ -q` → **583 passing** (was 551).
- `ruff check src/ tests/` and `ruff format src/ tests/` clean.
- Browser smoke from the Tailscale URL (not loopback): all 8 widgets
  paint; W1/W4/W5/W6/W7 reflect live numbers (compare to `/cost 24h`);
  W2/W3/W8 render their mock/derived data; range switcher
  (1h/24h/7d/30d) updates the token burn chart; auto-refresh toggle
  off → no further `/api/dashboard/all` calls; toggle on → calls every
  30s; "Hive" tab routes to `/`, "Dashboard" tab on landing routes to
  `/dashboard`.

### Out of scope (deferred to Sprint 21+)
- **W2 System health** real probes (postgres ping, claude API
  liveness, disk %, heartbeat gap detection). Currently all 5 strips
  hardcoded `ok` with synthetic summaries.
- **W3 Workload CFD** real anomaly detection ("widening for 3+ days").
  Currently a basic 7-day stacked series derived from `tasks.status`
  with stub anomaly windows.
- **W8 Failure scatter** classifier (`task.failure_reason` text →
  rate.limit/retry/crash/escalate). Currently empty array.
- Vite build pipeline (replacing Babel-in-browser if CDN payload
  becomes annoying).
- Multi-LLM routing, web OAuth, vault completeness audit — same
  deferrals as Sprint 19.

---

## Sprint 19 — Maestro Autonomy (2026-04-30, DONE)

**Status:** Complete
**Branch:** `sprint-19-maestro-autonomy` → main
**Totals:** 495 → 551 tests (+56). No migrations.

**Goal**: graduate the maestro from a router-and-reporter into the org's
CEO. Sprints 0–18 left the lead↔worker plumbing in place but the org
never grew itself: the user typed `/team create` and `/worker spawn`
manually, the priority field was decorative, and `_preempt_for_priority`
was dead code. Sprint 19 closes those three gaps so the maestro
auto-allocates teams/leads/workers within the `MAX_CONCURRENT_SESSIONS`
cap based on pending tasks, priority, idle-time, and Claude Max usage.

### Locked decisions
- **Workers get the same `MESSAGING_PROMPT` as leads/maestros.** The
  permission gates (`permissions.can_message`) already restrict who they
  can talk to (worker → lead per dotted-name prefix); cleaner than
  splitting prompts.
- **Three new action types** in `<hive_actions>`:
  - `spawn_team {team_name, lead_name, lead_personality?}` — maestro only.
  - `spawn_worker {team_name, worker_name, task_id?, personality?}` —
    maestro, or the lead of that team.
  - `kill_entity {target}` — maestro for own org; lead for own-team
    workers; never targets the default maestro.
- **CEO mechanism = scheduler + facts pipe**. Every
  `HIVE_PRIORITY_EVAL_INTERVAL_MINUTES` (default 120) the
  `PriorityScheduler` builds a facts prompt — free slots, pending tasks
  by priority, org snapshot with idle-time, 24h cost per entity — and
  sends it to each alive maestro via `send_to_entity`. The maestro
  decides allocation; the orchestrator stays a dumb facts pipe.
- **Spawn rate limit**: per-maestro `HIVE_AUTONOMOUS_SPAWN_LIMIT`
  (default 3) caps autonomous spawns per eval window. Over-the-limit
  spawns are rejected and audited as `entity.spawn_rate_limited`. The
  counter is keyed on the **root maestro** (`maestro_for_actor`) so a
  chatty lead can't bypass the cap by spawning under multiple sub-teams.
- **Preemption is a last-resort safety net**, not the primary capacity
  manager. Only fires inside `spawn_entity` when at cap; picks the
  lowest-priority **RUNNING** entity strictly worse than the new one.
  The default maestro is exempt — killing the org root would cascade.
  When `HIVE_PRIORITY_PREEMPT_ENABLED=false` the orchestrator hard-fails
  at cap instead of preempting.
- **`/eval [maestro]`** fires one tick on demand; **`/budget [maestro]`**
  prints the facts prompt without sending so the user can audit exactly
  what the maestro would receive.

### Files changed

| File | Change |
|------|--------|
| `src/hive/models/entity.py` | `MESSAGING_PROMPT` injection now includes `worker` role. |
| `src/hive/bus/actions.py` | Parser/validator extended for `spawn_team`, `spawn_worker`, `kill_entity`. |
| `src/hive/bus/permissions.py` | `can_spawn(actor, target_role, scope)` and `can_kill(actor, target_name, default_maestro)` gates. |
| `src/hive/process/manager.py` | Action dispatch routes the three new types; spawn dispatch consults `scheduler.can_autospawn`/`record_autospawn`; `_preempt_for_priority` skips the default maestro and audits `actor=system reason=preempt`; `spawn_entity` retries once after preempt when `PRIORITY_PREEMPT_ENABLED`. |
| `src/hive/process/loops.py` | `MESSAGING_PROMPT` documents the spawn/kill action types alongside `message`. |
| `src/hive/process/scheduler.py` (new) | `PriorityScheduler.build_facts_prompt`, `run_once`, `run_once_for`, `run(stop_event)`; per-maestro autonomous-spawn counter keyed on `maestro_for_actor`. |
| `src/hive/commands/dispatch.py` | `/eval` and `/budget` handlers; `scheduler` constructor kwarg threaded through. |
| `src/hive/telegram/commands.py` | `eval` and `budget` added to `targeted_commands`. |
| `src/hive/telegram/bridge.py` | `scheduler` kwarg threaded through to the inner `CommandDispatcher`. |
| `src/hive/telegram/help_text.py` | `/eval` and `/budget` help entries (Admin category). |
| `src/hive/config.py` | `HIVE_PRIORITY_EVAL_INTERVAL_MINUTES`, `HIVE_AUTONOMOUS_SPAWN_LIMIT`, `HIVE_PRIORITY_PREEMPT_ENABLED`. |
| `src/hive/__main__.py` | Constructs `PriorityScheduler`, sets `process_manager.scheduler`, runs `scheduler.run(stop_event)` as a background task; passes scheduler into `TelegramBridge` and the web `CommandDispatcher`. |
| `tests/test_actions.py` | +4 tests — parser coverage for the three new action types + malformed-payload rejection. |
| `tests/test_permissions.py` | +6 tests — spawn/kill gate matrix (maestro-can, lead-own-team-only, worker-cannot, never-default-maestro). |
| `tests/test_process_manager.py` | +5 tests — spawn_team / spawn_worker / kill_entity dispatch outcomes; rate-limit audit; unauthorised kill rejected. |
| `tests/test_scheduler.py` (new) | 11 tests — facts prompt structure (capacity, priority groups, own-tree filter, empty-state degradation), rate-limit window reset, run_once vs run_once_for distinction, run-loop clean shutdown + error swallowing. |
| `tests/test_preempt.py` (new) | 7 tests — default-maestro exemption, audit `actor=system`, spawn_entity retry-after-preempt, `PRIORITY_PREEMPT_ENABLED=false` hard-fail, IDLE entities ignored. |
| `tests/integration/test_lead_worker_roundtrip.py` (new, `@pytest.mark.integration`) | Real `claude -p` round-trip — lead delegates a task, worker emits `<hive_actions>`, lead's queue receives the reply. |

### Verification
- `pytest -m "not integration" -q` → **551 passing** (was 495).
- `journalctl --user -u hive` clean across `systemctl --user restart`.
- `/budget dev` from Telegram returns the facts prompt (capacity,
  pending by priority, org snapshot, 24h cost) without sending.
- `/eval dev` fires one tick; `/comms` shows the facts payload going
  to dev; if pending tasks exist the maestro replies with at least one
  autonomous action and `/audit` records the spawn/kill with `actor=dev`.
- Worker round-trip smoke: `/team create teamA leadA` →
  `/worker spawn teamA workerA` → `/a:dev.teamA.workerA "ping your lead"`
  → `/comms` shows the worker's reply on the lead's queue within ~30s.
- Preempt safety net: cap full of RUNNING entities, lowest-priority
  one strictly worse than P0, then `/priority P0 "..."` triggers the
  spawn — `/audit` shows `entity.kill actor=system reason=preempt`.

### Out of scope (deferred to Sprint 20+)
- Multi-LLM routing.
- Web OAuth (still on bearer-token + Tailscale).
- Vault completeness audit.
- Async-job model for `/api/command`.
- Codex CLI plugin.
- JSON-mode fallback for `<hive_actions>` (Phase 1 integration test
  showed natural-prompt emission is reliable enough for now).

---

## Sprint 18 — File Embedding Integration (2026-04-30, DONE)

**Status:** Complete
**Branch:** `sprint-18-file-embeddings` → main
**Totals:** 471 → 495 tests (+24), 1 migration (`018_attachment_embeddings.sql`).

**Goal**: close the loop opened in Sprint 17. Uploaded files now get a
Voyage embedding at upload time and are surfaced via the same
auto-retrieve flow that already injects matching blueprints into agent
prompts. Images go through `embed_multimodal`; PDFs and `text/*` files
get extracted then `embed_texts`. Other mime types stay searchable by
name only (NULL embedding, skipped by the search `WHERE`).

**Builds on**: Sprint 16 (multimodal embedder API was built for this) and
Sprint 17 (file transit + `attachments` table).

### Locked decisions
- **Schema**: extend `attachments` with `embedding vector(1024)` and
  `embed_text TEXT`. Single HNSW index. NULL rows are excluded from
  search via `WHERE embedding IS NOT NULL` — same pattern as blueprints.
- **Write path is split**: `attachment_store.save()` stays as before
  (durable row first); `update_embedding(id, vec, text)` is called
  after. If embedding raises, the row exists with NULL — the backfill
  script picks it up later via the same code path.
- **Embedding strategy by mime**:
  - `image/*` → `PIL.Image.open` → `thumbnail((1024,1024))` →
    `embed_multimodal([[image]])`. Thumbnail is mandatory — Voyage
    multimodal-3 caps at ~16MP.
  - `application/pdf` → `pypdf.PdfReader` → join page text → truncate
    to `ATTACHMENT_EMBED_MAX_CHARS` (default 8000) → `embed_texts`.
  - `text/*` → bytes → utf-8 with `errors="replace"` fallback →
    truncate → `embed_texts`.
  - Other → return `None`; column stays NULL.
- **Sync embedding**: runs inline after `save()` on both upload paths.
  Background tasks would introduce a "where's my embedding?" race for
  zero benefit; auto-retrieve is already on the user-facing critical
  path so the latency budget is shared.
- **Empty-extraction guard**: image-only PDFs and encrypted PDFs return
  empty strings from pypdf — skip + log + leave NULL.
- **Auto-retrieve renders two labeled blocks** (not interleaved by
  distance). Blueprints are consumed inline (body in the prompt); files
  are listed by path so the agent's first move is `Read`. Conflating
  them confuses the action the agent should take.
- **Config knobs** (env-overridable):
  - `HIVE_ATTACHMENT_EMBED_MAX_CHARS` (default `8000`)
  - `HIVE_AUTO_RETRIEVE_INCLUDE_ATTACHMENTS` (default `true`)
- **Backfill** is idempotent on `WHERE embedding IS NULL`. The same
  loop handles new-upload retries.

### Files changed

| File | Change |
|------|--------|
| `src/hive/bus/migrations/018_attachment_embeddings.sql` (new) | `embedding vector(1024)` + `embed_text TEXT` columns on `attachments`; HNSW cosine index. |
| `src/hive/bus/attachment_store.py` | New `update_embedding(id, vec, text)`; new `search(query, limit, max_distance)` mirroring `BlueprintStore`; new `list_unembedded()` for the backfill script; per-connection `_ensure_vector_codec`. |
| `src/hive/knowledge/attachment_embedder.py` (new) | `embed_attachment(path, mime)` with mime routing, image thumbnailing, PDF + text extraction, all four guards (encrypted PDF, empty extract, bad bytes, malformed). Returns `(vec, embed_text) \| None`. |
| `src/hive/web/app.py` | `/api/upload` calls `embed_attachment` + `update_embedding` after `save`; failure logs only, response is unchanged. |
| `src/hive/telegram/bridge.py` | Same wiring on `_handle_attachment`. |
| `src/hive/process/manager.py` | Constructor takes `attachment_store`; auto-retrieve also queries it; renders second labeled block under `AUTO_RETRIEVE_INCLUDE_ATTACHMENTS`. Blueprint and attachment failures isolated. |
| `src/hive/config.py` | New `ATTACHMENT_EMBED_MAX_CHARS`, `AUTO_RETRIEVE_INCLUDE_ATTACHMENTS`. |
| `src/hive/__main__.py` | Threads `attachment_store` into `ProcessManager`. |
| `pyproject.toml` | Added `pillow>=10`, `pypdf>=4`. |
| `scripts/backfill_attachment_embeddings.py` (new) | Idempotent CLI: pulls `list_unembedded()`, runs each through `embed_attachment` + `update_embedding`. Skips files that vanished. |
| `tests/knowledge/test_attachment_embedder.py` (new) | 14 tests — missing/unsupported, text/markdown happy path, truncation, encoding fallback, empty text skip, image happy path, oversized thumbnail, corrupt image, PDF happy path (mocked), encrypted PDF, empty PDF, `PdfReadError`, embedder failure. |
| `tests/fixtures/sample.png` (new) | 32×32 PNG fixture for the image happy-path test. |
| `tests/test_attachment_store.py` | +6 tests — `update_embedding`, `search` ordering, `max_distance` filter, NULL-row exclusion, `list_unembedded`. |
| `tests/process/test_auto_retrieve.py` | +3 tests — both blocks rendered, `INCLUDE_ATTACHMENTS=False` suppresses, attachment search failure isolated from blueprint search. |
| `tests/test_web_upload.py` | +1 regression — embedder failure still saves the file. |
| `tests/test_telegram_files.py` | +1 regression — same on the Telegram path. |
| `tests/test_advisor_mcp.py` | Set `pm.attachment_store = None` on the `__new__`-built test stub. |

### Verification
- `pytest tests/ -q` → **495 passing** (was 471).
- `ruff check src/ tests/ scripts/ && ruff format --check src/ tests/ scripts/` → clean.
- Migration 018 runs on service start; `\d attachments` shows
  `embedding | vector(1024)` and `embed_text | text`.
- Backfill: `python scripts/backfill_attachment_embeddings.py` →
  pre-existing uploads (`be4c9bc5...pdf`, `f7912b84...md`) get embeddings;
  second run is a no-op (`Skipped=0 Embedded=0`).
- Web smoke (Tailscale URL): upload an image → `psql -c "SELECT id, mime_type, embedding IS NOT NULL FROM attachments ORDER BY id DESC LIMIT 1"` shows `t`.
- Auto-retrieve smoke: `/m:dev` with a prompt referencing the uploaded
  file's content → response references the file by path; logs show the
  "Relevant uploaded files" block in the prompt.
- Failure-mode smoke: temporarily clobber `VOYAGE_API_KEY` →
  upload returns 200, row exists with `embedding IS NULL`, error logged.

### Out of scope (deferred to Sprint 19+)
- Multi-file per message (Telegram `media_group_id`, web multi-file forms).
- EXIF stripping / sanitization.
- File expiry / cleanup cron.
- Inline image rendering in the web chat.
- User-facing `/files search` command.

---

## Sprint 17 — File Transit (2026-04-28, DONE)

**Status:** Complete
**Branch:** `sprint-17-file-transit` → main
**Totals:** 450 → 471 tests (+21), 1 migration (`017_attachments.sql`).

**Goal**: enable Telegram and the web composer to attach files (photos,
PDFs, CSVs, Excel — anything ≤ 20 MB), persist them under
`data/uploads/`, and surface their absolute path to the targeted
maestro inside the prompt so Claude Code's `Read` tool can consume
them. **No embedding work** — that's Sprint 18. This sprint solves the
"how does a file get from a phone to the agent's filesystem at all"
problem in isolation.

**Builds on**: Sprint 15 (the shared `CommandDispatcher` extracted from
the Telegram bridge — both surfaces now reuse it for the routing call
on captioned uploads) and Sprint 16's joint text+image embedding
plumbing (the file path will become the input for Sprint 18).

### Locked decisions
- **Storage**: flat layout under `DATA_DIR / "uploads/"`, filenames are
  `{uuid4().hex}{ext}`. Outside `BLUEPRINTS_DIR` because uploads aren't
  blueprints — Sprint 18 may *promote* them, but raw transit lives in
  its own directory.
- **DB**: dedicated `attachments` table — minimal audit trail
  (path, mime, size, source `'telegram' | 'web'`, actor, optional
  `forwarded_to`). Not coupled to `blueprints`.
- **Routing**: caption (Telegram) or `text` field (web) is parsed by
  the existing `parse_command` and only routes if it resolves to
  `message`/`team`/`agent` (i.e. `/m:`, `/t:`, `/a:`, or plain text).
  `/status`, `/task`, etc. ignore the file but still log the upload.
- **Prompt shape**: surface prepends
  `[Attached file: {abs_path} ({mime}, {size} bytes, original: {name})]\n\n`
  to the user's text. No `ProcessManager` signature change — it's a
  surface-level concern.
- **Worker access**: yolo permission mode (default since Sprint 15
  polish) bypasses Read prompts so the absolute path "just works". No
  `--add-dir` plumbing needed.
- **File-size cap**: 20 MB (Telegram bot API hard limit), env var
  `HIVE_UPLOAD_MAX_BYTES` for tuning. Web mirrors the cap and returns
  413.
- **No type filter**: this sprint is dumb pipe — Sprint 18 decides what
  is/isn't embeddable.

### Files changed

| File | Change |
|------|--------|
| `src/hive/bus/migrations/017_attachments.sql` (new) | `attachments` table + `created_at DESC` index. |
| `src/hive/bus/attachment_store.py` (new) | `AttachmentStore` (`save`, `get`, `list_recent`) + `AttachmentMeta` dataclass. |
| `src/hive/config.py` | Added `UPLOADS_DIR = DATA_DIR / "uploads"` (mkdir on import) and `UPLOAD_MAX_BYTES` (env `HIVE_UPLOAD_MAX_BYTES`, default 20 MB). |
| `pyproject.toml` | Added `python-multipart>=0.0.9` (FastAPI `UploadFile` requirement). |
| `src/hive/telegram/bridge.py` | New `_handle_attachment` registered for `filters.PHOTO` + `filters.Document.ALL`; constructor takes `attachment_store`. Auth + size cap + uuid filename + caption-driven routing + audit log entry. |
| `src/hive/web/app.py` | New `POST /api/upload` (multipart). Streams to disk in 64 KiB chunks, aborts mid-stream on 413; persists via `attachment_store`; on routable caption builds enriched `Command` and dispatches via `CommandDispatcher.dispatch_command`. |
| `src/hive/web/templates/dashboard.html` | Paperclip button + hidden `<input type="file">` + chip showing the staged filename + JS branch in `sendChatCommand` that swaps `/api/command` for multipart `POST /api/upload` when a file is staged. Pre-flight 20 MB check on the client. |
| `src/hive/web/static/landing.css` | Styles for `.composer__attach`, `.composer__attach-chip`. |
| `src/hive/commands/dispatch.py` | `KNOWN_COMMANDS += {"files"}`; constructor takes `attachment_store`; new `_execute_files(args)` lists `list_recent(N)`. Helper `_format_bytes`. |
| `src/hive/telegram/help_text.py` | New `/files` entry under Resources (alphabetical: cost, files, model). |
| `src/hive/__main__.py` | Instantiates `AttachmentStore(pool)` and threads it into `TelegramBridge`, the web `CommandDispatcher`, and `create_app`. |
| `tests/conftest.py` | `attachment_store` fixture; `attachments` added to per-test TRUNCATE list. |
| `tests/test_attachment_store.py` (new) | 4 tests — save/get/list/orderings. |
| `tests/test_telegram_files.py` (new) | 7 tests — routable caption, no caption, document mime, oversize, unauth, plain caption, command caption skips routing. |
| `tests/test_web_upload.py` (new) | 6 tests — routing path, no-text store-only, command caption no-route, 413 oversize, 401 missing token, 503 no store. |
| `tests/test_command_dispatcher.py` | 4 new `/files` tests. |

### Verification
- `pytest tests/ -q` → **471 passing** (was 450).
- `ruff check src/ tests/ && ruff format --check src/ tests/` → clean.
- Migration 017 runs on service start; `\d attachments` shows the table.
- Telegram smoke: photo with caption `/m:dev describe this image` →
  dev's response references the image content; `/files 5` lists the
  upload with `→dev`.
- Web smoke (Tailscale URL): paperclip → pick PDF → text
  `/m:dev summarize` → dev's response renders in chat.
- `psql -c "SELECT id, source, mime_type, forwarded_to FROM attachments ORDER BY id DESC LIMIT 5"`
  matches.

### Out of scope (deferred to Sprint 18+)
- All embedding / blueprint integration of uploaded files.
- Multi-file per message (Telegram `media_group_id`, web multi-file forms).
- EXIF stripping / sanitization.
- File expiry / cleanup cron.
- Inline image rendering in the web chat.

---

## Sprint 16 — Voyage Embedding Migration (2026-04-28, DONE)

**Status:** Complete
**Branch:** `sprint-16-voyage-embeddings` → main
**Totals:** 446 → 450 tests (+4), 1 migration (`016_embedding_dim_1024.sql`).

**Goal**: Replace OpenAI `text-embedding-3-small` (1536d, text-only) with
Voyage `voyage-multimodal-3` (1024d, joint text+image) so the knowledge
store can eventually hold images as well as text. Independent driver:
the OpenAI key was leaked on 2026-04-26 via a faulty redaction regex
(`sk-[A-Za-z0-9-]*` didn't match underscores) and needed rotation —
this migration removes Hive's dependency on it entirely.

**Builds on**: Sprint 11 (semantic blueprints via pgvector) and the
Sprint 13 auto-retrieve flow — the embedding provider sits underneath
both.

### Locked decisions
- **Provider**: Voyage `voyage-multimodal-3`. Recommended by Anthropic;
  joint text+image embedding space; 1024d.
- **Existing data**: drop the 1 existing blueprint. 1536d and 1024d
  vectors live in different vector spaces, so there is no
  "convert" path — re-saving re-embeds via the new provider.
- **Migration order**: Voyage key in `.env` → deploy → smoke test →
  THEN revoke the OpenAI key. Revoking first crashes every
  `send_to_entity` until deploy lands.
- **`max_distance` filter**: new env var `AUTO_RETRIEVE_MAX_DISTANCE`
  (default `0.6`) suppresses lone-blueprint noise. With only 1 saved
  blueprint, every prompt would otherwise pull it in regardless of
  semantic relevance.
- **Multimodal scope**: ship the embedder API surface
  (`embed_multimodal`) but don't wire image inputs into bridge/web yet
  — that's a future sprint.

### Files changed
| File | Change |
|------|--------|
| `pyproject.toml` | Removed `openai>=1.30`; added `voyageai>=0.3` |
| `src/hive/config.py` | `OPENAI_API_KEY` → `VOYAGE_API_KEY`; `EMBEDDING_MODEL` default → `voyage-multimodal-3`; `EMBEDDING_DIM` default → `1024`; new `AUTO_RETRIEVE_MAX_DISTANCE` (default `0.6`) |
| `src/hive/knowledge/embedder.py` | Full rewrite. Voyage `AsyncClient` lazily constructed. `embed_texts` wraps each input as a single-segment doc and calls `multimodal_embed` (voyage-multimodal-3 doesn't support `embed`). New `embed_multimodal(inputs)` for future image use |
| `src/hive/knowledge/blueprints.py` | `search()` now accepts `max_distance: float \| None`; SQL branches into a `WHERE embedding <=> $1 < $threshold` filter when set |
| `src/hive/process/manager.py` | Auto-retrieve passes `AUTO_RETRIEVE_MAX_DISTANCE` to `blueprint_store.search()` |
| `src/hive/bus/migrations/016_embedding_dim_1024.sql` (new) | TRUNCATE blueprints; DROP HNSW index; ALTER COLUMN to `vector(1024)`; recreate HNSW index. pgvector cannot cast across dimensions, hence the truncate-first approach |
| `tests/knowledge/test_embedder.py` | Mock Voyage client instead of OpenAI; 1024d vectors; +2 multimodal tests (5 total) |
| `tests/knowledge/test_blueprints_pgvector.py` | 1024d one-hot fixtures keyed off `ord(t[0]) % 1024`; +1 `max_distance` filter test (5 total) |
| `tests/process/test_auto_retrieve.py` | 1024d mocks; +1 orthogonal-vector test for the `max_distance` filter (3 total) |
| `.env` (VPS) | Replaced `OPENAI_API_KEY` with `VOYAGE_API_KEY`; added `AUTO_RETRIEVE_MAX_DISTANCE=0.6` |
| `docs/DEPLOYMENT.md` | Documented the new env vars and the provider switch |

### Verification
- `pytest tests/ -q` → 450 passing.
- `ruff check src/ tests/ && ruff format --check src/ tests/` → clean.
- Migration 016 runs on service start; `\d blueprints` shows
  `embedding | vector(1024)`.
- `/blueprint save smoke-test "..."` succeeds → first Voyage call
  works.
- `/blueprint search sprint` returns the saved blueprint with a cosine
  distance score.
- `/m:dev` with a related prompt prepends blueprint context (visible
  in logs); with an unrelated prompt the `max_distance` filter
  suppresses it.
- `journalctl --user-unit hive.service -n 200 | grep -i openai` →
  empty.

### Out of scope (deferred)
- **Codex CLI + `codex-plugin-cc`** to absorb the $10 OpenAI credit
  balance: separate workstream.
- **Image embeddings end-to-end**: `embed_multimodal()` is wired but
  bridge/web input paths still only accept text.
- **Re-embed utility script**: skipped — only 1 blueprint and we're
  dropping it. Add later if Voyage releases a model upgrade we want
  to migrate to.
- **OpenAI refund**: user emails help.openai.com asking for the
  unused balance. Discretionary; worth trying since this sprint
  permanently removes the OpenAI dep.

---

## Post-Sprint 15 polish (2026-04-26, DONE)

**Status:** Complete (not a sprint — small UX/UI follow-ups after a real
day of using the web dashboard)
**Branch:** main (5 commits, all pushed)
**Totals:** 440 → 446 tests (+6), no migrations.

A day of dogfooding Sprint 15 surfaced five distinct UX gaps. Each
landed as its own small commit so the rollback story stays clean.

### 1. Viewport-locked layout + claude.ai-style composer (`ecb0b73`)
The chat rail used to grow forever and push the page off-screen. The
shell is now a single 100vh flex column: hero/vault/active/idle/dormant
sit in the scroll-region, the composer is a sticky footer. Composer
itself is a textarea autosizing on input, Enter-to-send /
Shift+Enter-for-newline, mirroring claude.ai. Dropping the old
single-line input also let us delete the floating "send" arrow.

### 2. Resizable rail + markdown-rendered bubbles (`7f6f051`)
The rail's width is now a drag-handle on its left edge, persisted in
`localStorage` so it survives reloads. Bubble bodies render markdown
(bold, italic, inline `code`, fenced blocks, links) via a small
escape-first renderer — no DOMPurify dependency, but the escape pass
keeps it XSS-safe. Code fences get monospace + paper-grey background.

### 3. Markdown table rendering (`1ec9207`)
GitHub-flavored tables (header / `|---|` separator / rows) render as
real `<table>` elements. The renderer was extended to recognise the
two-line header pattern before the existing inline pass.

### 4. Chat persistence + idle/dormant auto-refresh (`95293eb`)
Two bugs found by real use:
- **Chat history disappeared on reload.** `/api/command` was
  dispatching but never persisting to `MessageStore`. Added a pair of
  `log_message` calls (user → hive, hive → user) inside the endpoint
  inside a try/except so a write failure can't break the response.
- **New maestros / killed maestros didn't show up live.** The hero,
  vault, and active sections htmx-poll their fragments, but the idle
  and dormant sections were static. Added `/api/landing/idle` (5s) and
  `/api/landing/dormant` (30s) plus the corresponding `_partials/`
  templates.

### 5. Optimistic chat UI + yolo default + mode-request bubbles (`377838b`)
Three coupled issues from real use:
- **Long blocking wait after Send hides feedback.** `/api/command` is
  synchronous and a fresh-spawn `claude -p` round-trip is 5-30s; the
  user can't tell their message landed. The composer now paints the
  user bubble + a typing indicator (three pulsing dots) **before** the
  await, so feedback is instant. A full async-job rewrite is deferred.
- **New maestros denied tool calls under headless `claude -p`.** The
  default `permission_mode = "default"` means tool calls hit a
  permission prompt that has no UI in `-p` mode — the maestro
  narrates "click Allow" and stalls. `register_maestro` now sets
  `permission_mode = "yolo"` (`--dangerously-skip-permissions`) on new
  maestros. Existing persisted maestros keep their stored mode.
- **No web UI for mode-elevation requests.** When a maestro emits
  `<request_mode>yolo</request_mode>` and `approver == "user"`, the
  notification now carries structured `data` (id, requester, requested
  mode, reason). The browser SSE handler renders it as an inline
  bubble with Allow / Deny buttons that POST to two new endpoints:
  `/api/mode-request/{id}/approve` and `/api/mode-request/{id}/deny`.
  Telegram still works via `/approve mode N` — both surfaces are live.

`Notification` dataclass gained `data: dict | None`; `format_event`
forwards it to the SSE payload. Six new tests cover the endpoint pair
(auth gates, happy paths, 404 on missing/already-resolved rows).

### Verification
- `pytest tests/ -q` → 446 passing.
- `ruff check src/ tests/ && ruff format --check src/ tests/` → clean.
- Browser at `http://100.79.194.84:8080/`: composer paints instant
  feedback, new maestros register without permission stalls, mode
  bubbles approve/deny inline.

### Out of scope (deferred)
- **Async job model** for `/api/command` (return job-id immediately,
  stream tokens via SSE). Big architectural change; the optimistic UI
  buys most of the perceived improvement without the rewrite.
- **Token-by-token streaming** of maestro responses — same rewrite.
- **Backfilling `yolo`** for existing persisted maestros. Promote one
  with `/m:<name> mode yolo` per maestro as needed.
- **Mode-request bubbles for worker-to-maestro requests.** Today only
  `approver == "user"` requests fan out to notifications; surfacing
  maestro-approver requests would require routing them to the right
  maestro's chat, not the global rail.

---

## Sprint 15 — Web Write Surface + Multi-Channel Notifications (2026-04-25, DONE)

**Status:** Complete
**Branch:** main (merged direct)

**Goal**: Make the web dashboard a real second surface alongside Telegram —
issue commands, see chat history, get push notifications — without
retiring Telegram. Notifications fan out to Telegram, the browser tab
(SSE), and an email digest from a single dispatcher.

**Builds on**: Sprint 14 (read-only A.2 landing page), Sprint 1
(MessageStore.get_recent), the existing Telegram bridge.

**Totals**: 387 → 440 tests (+53), 5 commits on `main`, 5 new modules
(`commands/dispatch.py`, `notifications/dispatcher.py`,
`notifications/email.py`, `web/auth.py`, `web/sse.py`).

### Phase 1 — Extract CommandDispatcher (refactor)
Pulled the 34 manager-backed command paths out of
`telegram/bridge.py:_execute_command` into a transport-agnostic
`CommandDispatcher.dispatch(text, actor) -> CommandResult`. Bridge is
now a thin adapter that parses Telegram updates, calls the dispatcher,
and formats the reply for Telegram. No behavior change. Commit `06ad29b`.

### Phase 2 — NotificationDispatcher + Telegram channel
Replaced `ProcessManager.set_notification_callback()` (single sink) with
`NotificationDispatcher` — channels register and each receives every
event with per-channel error isolation. TelegramBridge becomes the first
channel. Notification dataclass carries `text`, `kind`, `timestamp`.
Commit `8d9f0ae`.

### Phase 3 — Web write endpoints + bearer-token auth
- `POST /api/command` (auth-gated) routes through CommandDispatcher with
  `actor="web:user"`.
- `GET /api/messages?limit=20` exposes recent messages from MessageStore;
  read access stays gated by Tailscale-only network bind.
- `web/auth.py` exposes `require_token` as a FastAPI dependency. Empty
  `HIVE_WEB_TOKEN` rejects all writes — disabled-closed, never open.
- `view_model.chat.messages` populated from the store; the static "use
  Telegram" stub is gone.
- Dashboard chat input becomes a real form. Commit `cd4ac96`.

### Phase 4 — SSE broker + live chat-rail updates
- `web/sse.py` adds `SSEBroker` with bounded per-subscriber asyncio
  queues. Slow consumers drop their oldest event so a stalled tab can't
  back-pressure Telegram or email.
- `GET /sse/notifications` emits `text/event-stream` with an immediate
  `:ready` comment frame for connection confirmation.
- `require_token` now also accepts `?token=` (browsers' EventSource
  cannot set custom headers).
- Dashboard subscribes on load and appends incoming events directly into
  the chat rail — no more page reloads. Commit `9b70957`.

### Phase 5 — Email digest channel
- `notifications/email.py` adds `EmailDigest`, a notification channel
  that buffers events and flushes on size or interval. Console mode
  (logs the digest) when SMTP_HOST is unset, so it's exercisable on dev
  hosts without credentials. Commit `20c08f0`.

### New env vars
| Var | Default | Purpose |
|-----|---------|---------|
| `HIVE_WEB_TOKEN` | (empty) | Bearer token for write surface; empty disables writes |
| `HIVE_EMAIL_ENABLED` | `false` | Toggle email digest |
| `HIVE_EMAIL_TO` | (empty) | Recipient address |
| `HIVE_SMTP_HOST` | (empty) | SMTP server (empty = console mode) |
| `HIVE_SMTP_PORT` | `587` | SMTP port |
| `HIVE_SMTP_USER` / `HIVE_SMTP_PASSWORD` | (empty) | SMTP auth |
| `HIVE_EMAIL_DIGEST_INTERVAL_MINUTES` | `60` | Time-based flush |
| `HIVE_EMAIL_DIGEST_BUFFER_SIZE` | `20` | Size-based flush |

### Verification
- `pytest tests/ -q` → 440 passing.
- `ruff check src/ tests/ && ruff format --check src/ tests/` → clean.
- Browser at `http://100.79.194.84:8080/`: chat rail shows real recent
  messages, typing `/help` posts to the API and the response appears
  inline, mode-request triggers stream into the rail via SSE.

### Out of scope (deferred)
- Multi-user auth (OAuth/sessions) — single-user shared bearer token.
- WebSocket — SSE is enough for one-way notifications.
- Per-channel notification routing ("P0 to email only") — every channel
  gets every event for now.
- Email rich templates — plain-text digest in v1.
- Telegram retirement — explicitly kept; revisit after web parity.

---

## Sprint 14 — Web Landing Page (A.2 Paper Ops) (2026-04-25, DONE)

**Status:** Complete
**Branch:** main (merged direct)

**Goal**: Replace the dark-slate htmx dashboard at `/` with the A.2 Paper Ops
landing page from `claude.ai/design`, server-rendered and wired to live Hive
state. Auth deferred to a follow-up sprint — site stays bound to `127.0.0.1`
and is reached via Tailscale.

**Builds on**: Sprint 2b (TaskStore), Sprint 6 (VaultStore), Sprint 12
(ModeRequestStore), the existing FastAPI + Jinja2 + htmx web stack.

**Totals**: 378 → 387 tests (+9), 2 commits on `main`, 1 new module
(`view_model.py`), 4 new templates (`_macros.html` + 3 partials), 1 new
stylesheet (`landing.css`, ~530 lines).

### Phase 1 — Visual shell with mock data
**Files**
- `src/hive/web/static/landing.css` (new) — design tokens lifted from
  `direction-a3.jsx` into CSS custom properties (paper, ink, accent, honey,
  sage, vault). Layout for `.shell`, `.main-row`, `.content`, `.top-bar`,
  `.chat-rail`, `.hero`, `.maestro-card`, `.vault-card`, `.idle-strip`,
  `.dormant-pill`, `.terminal-bar`. Animations: `a3-pulse-ring`, `a3-badge`,
  `a3-hum`, `a3-bob`.
- `src/hive/web/templates/_macros.html` (new) — shared Jinja2 macros
  (`bee`, `hex`, `state_dot`, `pri_pill`, `tasks_chip`, `maestro_card`)
  extracted into a dedicated file so partials can render standalone without
  triggering a full `dashboard.html` render at macro-import time.
- `src/hive/web/templates/dashboard.html` (rewritten) — A.2 layout with top
  bar, chat rail, hero strip (htmx every 30s), pinned PA + Vault cards
  (vault polled every 15s), active maestros grid (every 5s), idle list,
  dormant pills, terminal bar.
- `src/hive/web/templates/_partials/{hero,vault,active}.html` (new) — htmx
  `innerHTML` swap targets so the wrapper elements with `hx-get` attributes
  persist across polls.
- `src/hive/web/app.py` — added `STATIC_DIR` mount at `/static`, three
  fragment endpoints (`/api/landing/{hero,vault,active}`), a temporary
  `_mock_view_model()` helper for Phase 1.
- `src/hive/__main__.py` — passes `vault_store` and `mode_request_store`
  into `create_app(...)`.

### Phase 2 — Wire live process state
**Files**
- `src/hive/web/view_model.py` (new) — `build_landing_view_model(...)`
  async function. Reads `process_manager.entities`, splits maestros into
  active/idle by `EntityState`, scans `personalities/*.md` for dormant
  candidates, queries `vault_store.pending("vault")` and
  `mode_request_store.list_pending(default_maestro)` for approvals, and
  returns the dict shape consumed by `dashboard.html` and the htmx
  partials. Maps integer `task.priority` → `P{n}` strings at the
  view-model layer (templates expect strings).
- `src/hive/web/app.py` — `_build_view()` now delegates to
  `build_landing_view_model`; mock helper removed.
- `src/hive/__main__.py` — passes `default_maestro=DEFAULT_MAESTRO` and
  `personalities_dir=PERSONALITIES_DIR` into `create_app(...)`.

### Phase 3 — Tests + docs
**Files**
- `tests/test_web_landing.py` (new) — 9 tests across landing page render,
  fragment endpoints, view-model shape, registered-maestro promotion to
  active section, vault pending counting, and dormant detection from
  personalities directory.
- `docs/PROJECT_PLAN.md` — this section.
- `docs/DEPLOYMENT.md` — `HIVE_WEB_PORT` setup and Tailscale URL pattern.

### Verification
- `pytest tests/ -q` → 387 passed.
- `ruff check src/ tests/` → clean.
- `curl http://127.0.0.1:8080/` → returns the A.2 page with `Hive — Landing`
  in the title.
- `curl http://127.0.0.1:8080/api/landing/{hero,vault,active}` → all 200,
  htmx-shaped HTML fragments.
- Browser via Tailscale (`http://<tailscale-host>:8080/`) — bee mascot
  animates, top bar shows correct counts, sections populate from real
  state, htmx polls fire every 5/15/30s without errors.

### Out of scope (deferred)
- Web auth (OAuth / session cookies) — `127.0.0.1` binding only.
- Interactive chat-from-web — v1 shows a static "talking to /m:dev"
  placeholder reading the most-recent MessageStore conversation.
- WebSocket live updates (htmx polling is enough at personal scale).
- Drag-to-pin maestros (visual cue only in v1).
- Multi-page navigation — only "Hive" tab is functional.

### Deployed 2026-04-25 11:25 UTC

`HIVE_WEB_PORT=8080` added to `.env`, service restarted, all six BEM
classes (`top-bar`, `hero__title`, `maestro-card`, `vault-card`,
`chat-rail`, `terminal-bar`) present in rendered HTML, fragment
endpoints return 200.

**Follow-up (2026-04-25, ~11:55 UTC)**: initial bind was
`127.0.0.1:8080` (the project's hardened default), so Tailscale peers
got `ERR_CONNECTION_REFUSED`. Curl-from-VPS smoke check missed it
because loopback worked fine. Fixed by setting
`HIVE_WEB_HOST=100.79.194.84` (the VPS's Tailscale IP), which makes
the bind tailnet-only as a socket-level constraint. Reachable from any
tailnet device at `http://100.79.194.84:8080/` (or via MagicDNS
short name `http://ubuntu-s-4vcpu-8gb-sgp1-01:8080/`). Lesson: post-
deploy verification must hit the actual access path, not just
loopback.

---

## Sprint 13 — Command UX, Observability, Entity Self-Review (2026-04-19, DONE)

**Status:** Complete
**Branch:** sprint-13-cmd-ux

### Changes shipped:
1. **opusplan model** — `/model opusplan <entity>` sets the entity to use Claude's opusplan alias (Opus for planning, Sonnet for execution).
2. **Loop yolo → ship-it** — Renamed the `yolo` loop mode to `ship-it` to avoid collision with `/mode yolo` (dangerous permissions). DB migration 014 handles existing rows automatically.
3. **Help improvements** — Every command now has usage examples in `/help <command>`. Flat `/help` listing shows count header. Descriptions standardized to ≤80 chars.
4. **Heartbeat scheduler** — New `/heartbeat on|off|status|<minutes>` command. Sends periodic status pings to Telegram with entity health summary. Controlled via `HIVE_HEARTBEAT_ENABLED` env var.
5. **MCP advisor server** — Each entity spawns a stdio MCP server exposing an `advisor()` tool backed by Opus. Entities call it for second-opinion review. Rate-limited to 5-min cooldown + 20 calls/day per entity. Audit log in `advisor_calls` table.

---

## Sprint 12 — Self-Dev Readiness Bundle (2026-04-18, DONE)

**Goal**: Make Hive capable of developing Hive with minimal human babysitting —
/help inventory, yolo/yotree modes with approval gates, Telegram-driven git
flow, and auto-retry on task failures.

**Builds on**: Sprint 10 (notification callback, audit namespaces), Sprint 9
(`<hive_actions>` inter-agent protocol), Sprint 2b (approval pattern from
VaultStore).

**Totals**: 275 → 350 tests (+75), 4 commits on `main`, 2 new migrations (012
mode_requests, 013 task_retries), 3 new Telegram commands (/commit, /pr,
/merge), 2 new permission modes (yolo, yotree), 2 new approval commands
(/approve, /deny), 1 new /help command.

### Phase 1 — `/help` command (2026-04-18, DONE)

**Files**
- `src/hive/telegram/help_text.py` (new) — `HelpEntry` dataclass, `HELP_TEXT`
  dict covering every Telegram command grouped into 10 categories (Status,
  Organization, Messaging, Tasks, Session, Resources, Security, Knowledge,
  Git, Admin). `format_all()` renders the grouped listing; `format_one(name)`
  renders per-command detail with usage, description, and examples.
- `src/hive/telegram/commands.py` — added `"help"` to `targeted_commands` so
  `/help <cmd>` parses the target correctly.
- `src/hive/telegram/bridge.py` — new module-level `BRIDGE_COMMANDS` frozenset
  (source of truth for the drift test), new `_execute_help(name)` handler,
  dispatch line added.
- `tests/test_help.py` (new) — 14 tests including two drift guards
  (`test_every_bridge_command_has_help_entry` and
  `test_every_help_entry_is_a_real_command`) that ensure HELP_TEXT and
  BRIDGE_COMMANDS stay in sync as new commands are added.
- `docs/DEPLOYMENT.md` — added `/help` to the Telegram commands section.

**Verification**
- `pytest` — 289 tests passing (up from 275).
- `ruff check src/hive/telegram/` clean.
- Smoke test via `format_all()` confirms every command renders and output
  fits well under Telegram's 4096-char message limit.

### Phase 2 — Yolo/yotree modes + hierarchical approval (2026-04-18, DONE)

Unlocks `--dangerously-skip-permissions` behind an approval gate. Entities
that aren't the user's direct maestro can't elevate themselves: they emit a
`request_mode_change` hive action, which routes to their approver (worker →
lead → maestro → user). The user receives a Telegram notification for
maestro-level requests and resolves with `/approve mode <id>` or
`/deny mode <id> [reason]`.

**Files**
- `src/hive/bus/migrations/012_mode_requests.sql` (new) — `mode_requests`
  table: `id`, `requester`, `requested_mode`, `approver`, `status`
  (pending/approved/denied/expired), `reason`, `created_at`, `resolved_at`.
- `src/hive/bus/mode_request_store.py` (new) — shape mirrors `VaultStore`:
  `create`, `get`, `list_pending(approver)`, `approve`, `deny`,
  `expire_older_than`, `recent`.
- `src/hive/models/entity.py` — extended `PERMISSION_MODES` with `yolo` and
  `yotree` sentinels; added `DANGEROUS_MODES` frozenset; `build_cli_args`
  emits `--dangerously-skip-permissions` for both modes.
- `src/hive/bus/actions.py` — new `request_mode_change` action type
  (fields: `requested_mode`, optional `reason`).
- `src/hive/process/manager.py` — constructor accepts `mode_request_store`;
  new `_approver_for`, `request_mode_change`, `approve_mode_request`,
  `deny_mode_request`, `expire_old_mode_requests`; `send_to_entity` dispatches
  `request_mode_change` actions on the response.
- `src/hive/telegram/bridge.py` — new `_execute_approve(sub, args)` and
  `_execute_deny(sub, args)` handlers; dispatch + `BRIDGE_COMMANDS` updated;
  `_execute_mode` now renders the `--dangerously-skip-permissions` preview
  for yolo/yotree.
- `src/hive/telegram/commands.py` — added `approve`, `deny` to
  `targeted_commands` so `/approve mode <id>` parses.
- `src/hive/telegram/help_text.py` — entries for `/approve`, `/deny` added
  under Security; `/mode` entry updated with yolo/yotree.
- `personalities/_template.md`, `personalities/maestro-dev.md` — new
  "Permission modes" section biasing entities toward `yotree` for code work
  and requiring non-user entities to request elevation via
  `request_mode_change`.
- `tests/test_mode_approval.py` (new) — 14 tests across approver
  resolution, row persistence, notification routing, approve/deny state
  machine, and expiry.
- `tests/test_actions.py` — added `TestRequestModeChangeAction` class
  covering the new action's parse paths.

**Audit categories**: `mode.request`, `mode.approve`, `mode.deny`,
`mode.expire`.

### Phase 3 — Git workflow commands (2026-04-18, DONE)

Telegram drives git/gh from a worker's worktree. Three new commands layer on
top of the Phase 2 approval store's design idea (re-using "the person
pressing the button is the authorization") — `/merge` is gated by the
environment variable `HIVE_ALLOW_AUTO_MERGE=1` rather than an approval row,
since running the command in Telegram already funnels through the
user-allowlist.

**Files**
- `src/hive/process/git_ops.py` (new) — async wrappers around `git` and
  `gh`: `run`, `commit` (stages + commits + returns SHA and shortstat),
  `current_branch` (returns empty string on detached HEAD),
  `push` (`git push -u origin <branch>`), `gh_pr_create` (`--fill` when no
  title), `gh_pr_merge` (`--squash --delete-branch`). Extracted so tests can
  monkeypatch the subprocess runner without stubbing asyncio.
- `src/hive/telegram/bridge.py` — new `_execute_commit`, `_execute_pr`,
  `_execute_merge` handlers; `_worktree_for` helper; dispatch lines.
- `src/hive/telegram/commands.py` — added `commit`, `pr`, `merge` to
  `targeted_commands`.
- `src/hive/telegram/help_text.py` — new "Git" category with entries for
  all three commands.
- `src/hive/config.py` — `ALLOW_AUTO_MERGE = os.environ.get(...) == "1"`.
- `tests/test_git_commands.py` (new) — 10 tests covering usage errors,
  missing worktree, successful commit (verifies the git add/commit sequence
  and SHA parsing), propagated git failure, pr push+create with title, pr
  with `--fill` fallback, detached-head refusal, merge disabled by default,
  and successful merge when the env flag is set.

### Phase 4 — Auto-recovery on task failures (2026-04-18, DONE)

Tasks now carry retry bookkeeping (`retry_count`, `max_retries`,
`failure_reason`). When a task-bound prompt fails the orchestrator retries
up to `max_retries` on the same entity with the failure reason prepended,
then escalates one rung upward — worker → lead → maestro → user
(Telegram). At user level the existing notification callback fires so the
on-call operator gets a ping; intermediate escalations route an inter-agent
inbox message so the parent can re-assign or abort.

**Files**
- `src/hive/models/task.py` — added `retry_count`, `max_retries`,
  `failure_reason` dataclass fields.
- `src/hive/bus/migrations/013_task_retries.sql` (new) — three columns with
  safe defaults so existing rows remain valid.
- `src/hive/bus/task_store.py` — new `increment_retry(task_id, reason)` and
  `update_failure(task_id, reason)`; row mapper updated.
- `src/hive/bus/actions.py` — new `report_failure` action type (fields:
  `reason`, optional `task_id`).
- `src/hive/process/manager.py` — `task_store` constructor arg; new
  `_task_id_for`, `_escalation_target_for`, `handle_task_failure`;
  `send_to_entity` dispatches `report_failure` actions.
- `src/hive/__main__.py` — wires `task_store` into `ProcessManager`.
- `tests/test_auto_recovery.py` (new) — 14 tests covering model defaults,
  store increments, action parsing, retry prompt assembly, worker → lead
  router escalation, maestro → user TG notification, audit emission, and
  status preservation under retry.

**Audit categories**: `task.retry`, `task.escalated`, `task.gave_up`.

### Sprint 12 verification

- `pytest` — 350 passing (up from 275).
- `ruff check src/ tests/` — clean.
- `ruff format --check src/ tests/` — clean.
- 4 commits on `main` between `b5e2064` and `f32e8a0`.

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
