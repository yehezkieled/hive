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
