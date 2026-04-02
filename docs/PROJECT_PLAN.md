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
