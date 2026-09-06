# Research — Ticket 045: CommandRouter registry + read-only Formatter split

**Method.** A 5-agent parallel exploration (callers / tests / ADR-0006 precedent /
help-guard, plus a full manual read of all 1367 lines of `dispatch.py` for the
dependency matrix — the agent on that one tripped its output cap). Every claim
below carries a `file:line` ref. This is a **no-behaviour-change** ticket, so the
research target is *exactly what the code does today* before anything moves.

---

## Q1 — Caller contract (what must stay backward-compatible)

Three production construction sites + tests, all passing the **same 8-store
`__init__`**:

| Caller | Where | Note |
|--------|-------|------|
| Telegram bridge | `src/hive/telegram/bridge.py:86-97` | `blueprint_store=None` |
| Web dispatcher | `src/hive/__main__.py:365-376` | full stores incl. `blueprint_store`; built only when `WEB_PORT > 0` |
| Tests | `tests/test_command_dispatcher.py:39-61` | injects all stores, `default_maestro="dev"` |

- **Constructor signature is load-bearing** (`dispatch.py:138-163`): `ProcessManager`
  + 8 optional stores + `default_maestro` + `personalities_dir`. The facade split
  **must preserve this exact signature** — collaborators are constructed *inside*
  `__init__`, never exposed to callers.
- **Two public entry points, both must stay:**
  - `dispatch(text, actor)` (`:165-188`) — parses text, runs the stateful `/new
    maestro` flow, then calls `dispatch_command`. Used by web `app.py:168`.
  - `dispatch_command(cmd, actor)` (`:194-353`) — executes a parsed `Command`.
    Used by bridge `bridge.py:375`, web `app.py:287,337`.
- **`CommandResult` is a 4-field contract** (`:113-127`): `.text` (always read),
  `.routed` (web dedups message logging on it — `app.py:168-180`), `.entity`
  (routing target), `.metadata`. **The `routed` flag is critical** — break it and
  web message history double-logs.
- **Instance state stays on the facade:** `self._pending_new` (`:163`) keys the
  in-flight `/new maestro` Q&A per actor. It cannot move into a stateless
  collaborator; the `/new` handler stays facade-side (or a collaborator that the
  facade owns and that reaches state through the back-ref).

## Q2 — The read-only need is real (the motivation)

The 6 web GET endpoints (`/api/status`, `/api/org`, `/api/tasks`, `/api/cost`,
`/api/audit`, `/api/commands`) at `src/hive/web/app.py:85-145` **already bypass
the dispatcher** — they read `process_manager` + stores directly. The
`CommandDispatcher` is handed to `create_app` but used **only** for the two POST
write paths (`/api/command`, `/api/upload`).

**Implication:** the Formatter split's payoff is *future* read endpoints (and
de-duplicating the ad-hoc rendering already in `app.py`) being able to call
`Formatter(pm).status()` instead of either hand-rolling the view or building the
whole mutation machinery. The acceptance bar ("Formatter needs only a
ProcessManager") is about that constructor cost — see the Q9 tension below.

## Q3/Q4 — Dependency matrix + cross-group coupling (hand-derived, full read)

Every handler, the instance attributes it touches, and read-only vs mutating.
"PM" = `process_manager`. Grouped by the ticket's proposed collaborators.

### FORMATTER (read-only views)
| Cmd | Method:line | Touches | R/W |
|-----|-------------|---------|-----|
| status | `_format_status` :1234 | PM.get_status | R |
| org | `_format_org` :1217 | PM.entities | R |
| teams | `_format_teams` :1199 | PM.entities | R |
| quota | `_format_quota` :453 | PM.quota_monitor | R |
| help | `_execute_help` :359 | *(module `help_text`)* | R |
| maestros | **inline** :208 | PM.entities | R |
| comms | **inline** :219 | PM.router.store | R |
| health | **inline** :202 | PM.health_check | R |
| cost | `_format_cost` :466 | **token_store** | R |
| audit | `_format_audit` :543 | **audit_log** | R |
| tasks | `_format_tasks_list` :622 | **task_store** | R |

→ **Tension (Q9):** status/org/teams/quota/help/maestros/comms/health are
PM-only, but **cost/audit/tasks read three stores** (token/audit/task). The
ticket's literal Formatter list is *status/org/teams/quota/cost/audit/help* —
i.e. it already includes cost+audit, which are *not* PM-only. So "needs only a
ProcessManager" can't be literal. Resolve in design.

### DATASTORE (vault + blueprint)
| Cmd | Method:line | Touches | R/W |
|-----|-------------|---------|-----|
| vault | `_execute_vault` :955 | **vault_store** + PM.approve/deny_vault_action | R+W (real money, ADR 0017) |
| blueprint | `_execute_blueprint` :1022 | **blueprint_store** | R+W |

### GIT (commit / pr / merge / files?)
| Cmd | Method:line | Touches | R/W |
|-----|-------------|---------|-----|
| commit | `_execute_commit` :1108 | `_worktree_for` + git_ops + **audit_log** | W |
| pr | `_execute_pr` :1133 | `_worktree_for` + git_ops + **audit_log** | W |
| merge | `_execute_merge` :1164 | `_worktree_for` + git_ops + audit_log + `ALLOW_AUTO_MERGE` | W |
| files | `_execute_files` :592 | **attachment_store** | R |
| *(helper)* | `_worktree_for` :1096 | PM.entities | — |

→ **Tension (Q11/scope):** `/files` is in the ticket's Git list but by dependency
it's a **read-only attachment listing** (no git, no worktree) — it fits Formatter
far better than Git. Flag for design.

### FACADE-RETAINED (lifecycle / messaging / approvals — *not* extracted)
`kill` (:228 inline), `message`/`agent` (`_send_to_entity` :636), `task`
(`_execute_task` :494, +audit), `priority` (:784, +audit), `team` (:661,
create/kill + dot-routing branch :264), `project` (:690), `mode` (:736),
`loop` (:766), `swarm` (:816), `new`/`_advance_new_flow`/`_finalize_new_maestro`
(:844/875/887, **stateful**), `personality` (:904), `broadcast` (:921), `model`
(:940), `compact` (:1063), `reset` (:1077), `approve`/`deny`
(`_execute_approve`/`_deny` :365/414, mode_request_store + gate flow), `eval`
(:558, scheduler), `budget` (:577, scheduler), `cancel`/`empty` (inline).

### Cross-group couplings (the splittability check)
1. **`_execute_team` "list" calls `self._format_teams()`** (`:679`) — a
   facade-retained command reaching into the Formatter. After split → call
   through the facade (`self._dispatcher.formatter...`). Minor, expected.
2. **`audit_log` spans three groups** — Formatter (reads, `/audit`), Git (writes,
   commit/pr/merge), Facade (writes, task/priority). Fine under the back-ref
   pattern: all reach `self._dispatcher.audit_log`. No copy.
3. **`_send_to_entity`** (`:636`) is shared by message/agent/team-dot/swarm/
   broadcast — **all facade-retained**, so it stays on the facade. No cross-edge.
4. **`_worktree_for`** (`:1096`) used only by commit/pr/merge — all Git. Moves
   with the Git group cleanly.
5. **~6 commands are inline in `dispatch_command`** (no `_execute_*` method):
   `empty`, `health`, `maestros`, `comms`, `kill`, `cancel`, + the `team`
   dot-routing branch. **The registry needs every command to map to a callable**,
   so these must be lifted into handler methods as part of the registry phase.

**Verdict:** the split is achievable. The only true coupling is #1 (one
Formatter call from a facade command), trivially handled by the back-ref pattern.
No collaborator-to-collaborator import is required.

## Q5 — ADR 0006 precedent (the exact template to mirror)

From `docs/adr/0006-...md` + the live `process/` split:

- **Facade constructs collaborators in `__init__` and stores them as
  attributes** — `manager.py:190-196`: `self.lifecycle = LifecycleManager(self)`,
  `self.approvals = ApprovalHandler(self)`, etc.
- **Each collaborator takes one ctor arg — a back-reference to the facade —
  stored as `self._mgr`, with the facade type under `TYPE_CHECKING`** to break
  the import cycle (`lifecycle_manager.py:43-44,186-187`; same in
  `approval_handler.py`, `message_dispatcher.py`, `wake_scheduler.py`).
- **Facade methods become one-line delegations** (`manager.py:342-354,557-558`).
- **Shared state is mutated *through* the back-ref** — `self._mgr._entities[...]`,
  and introspection lists are reset by **rebinding through `self._mgr`**
  (`message_dispatcher.py:9-14` warning), never a local rebind or `.clear()`.
- **Collaborators never import each other** — they call across only via the
  facade's public surface (ADR 0006:38-40).
- **One module per collaborator**, named after the class
  (`lifecycle_manager.py` → `LifecycleManager`).
- **Acceptance precedent:** each new module gets its own isolated test file +
  zero public-API breakage.

→ **045 template:** create `commands/formatter.py` (`Formatter`),
`commands/datastore_commands.py` (`DataStore`), `commands/git_commands.py`
(`GitCommands`); each `__init__(self, dispatcher)` stores `self._dispatcher`
(under `TYPE_CHECKING`); facade builds them in `__init__` and delegates.
ADR 0017 (ownership guard) and ADR 0008 (skill denylist) are **orthogonal** — the
DataStore split is code-clarity only, **not** a security boundary
(`adr/0017:41-58`).

## Q6 — Help drift-guard: registry as the single source of truth

- `KNOWN_COMMANDS` is a **38-entry hardcoded frozenset** (`dispatch.py:69-110`),
  re-exported via `commands/__init__.py` and consumed by the bridge to derive
  `BRIDGE_COMMANDS = KNOWN_COMMANDS | {"heartbeat"}` (`bridge.py:48`).
- `heartbeat` is **bridge-only** — in `HELP_TEXT` + `BRIDGE_COMMANDS` but **not**
  `KNOWN_COMMANDS` and not a dispatcher arm (`help_text.py:59-69`,
  `bridge.py:372-373`, asserted at `test_command_dispatcher.py:74`).
- The drift guard lives in `tests/test_help.py:14-23` — **bidirectional**:
  `HELP_TEXT.keys() == BRIDGE_COMMANDS`.

→ **Plan:** `KNOWN_COMMANDS = frozenset(REGISTRY)` (derive, don't hand-maintain).
Keep `BRIDGE_COMMANDS` deriving from it + `{heartbeat}`. The drift test stays
anchored on `BRIDGE_COMMANDS`, not `KNOWN_COMMANDS` (else heartbeat falls out).
Add a guard: **registry keys == KNOWN_COMMANDS** so a new arm can't skip help.

## Q7 — Test + coverage baseline

- Primary: `tests/test_command_dispatcher.py` (34 funcs, ~15 commands covered) +
  `tests/test_git_commands.py` (commit/pr/merge). ~24 commands are thin (parser-
  only): agent/blueprint/broadcast/budget/comms/compact/cost/eval/loop/maestros/
  message/mode/model/org/personality/priority/project/quota/reset/swarm/tasks/
  team/teams/vault.
- **Mocking pattern to reuse:** a *live* `ProcessManager(router=...)` with a
  `FakeAdapter` injected via `using_adapter()` (`tests/fakes.py:22-124`); the 8
  stores are **real DB objects** from session-scoped Postgres fixtures in
  `conftest.py:84-178` (truncated per test). `git_ops.run` is monkeypatched.
- **Coverage floor: `fail_under = 75`** in `pyproject.toml:55-61` (ticket 011);
  current ~77.4%. CI runs `pytest -m "not integration" --cov`
  (`.github/workflows/ci.yml:28-29`). New per-group tests must keep coverage
  **≥ floor** — extracting thin/untested handlers into their own modules *lowers*
  their module-local coverage unless tests are added, so each group needs real
  unit tests (this is the acceptance criterion anyway).

---

## Open design tensions → resolved in `design.md`

1. **Registry callable shape (Q8).** Handlers are heterogeneous: sync vs async;
   `(target, args)` vs `(target, args, actor)` vs `(target)` vs `(args)`; some set
   `routed/entity`; `team` has a dot-routing pre-branch. Need one uniform
   adapter shape — recommend `name → async (cmd, actor) -> CommandResult`, each a
   thin per-command wrapper; `empty` + `team`-dot stay special-cased.
2. **Formatter purity (Q9).** cost/audit/tasks need read-only stores. Recommend:
   Formatter takes `(process_manager, token_store, audit_log, task_store)` — i.e.
   *read-only* stores allowed, *approval/mutation* stores (vault/mode_request/
   blueprint) banned. That honours the acceptance intent ("no approval/mutation
   stores") without the impossible literal "PM-only".
3. **`/files` placement (Q11).** Read-only over `attachment_store` — belongs in
   Formatter, not Git, by dependency. Recommend moving it to Formatter (and noting
   the ticket's Git-list wording was nominal).
4. **Lane (Q10).** Every phase rewrites the one file → **direct lane**, one PR,
   sequential commits (registry → Formatter → DataStore → Git). Fan-out would
   rebase-collide on `dispatch.py`.
5. **Group scope (Q11).** Hold all three groups (ticket commits to them) — the
   coupling check says it's clean.
6. **Inline-handler lift.** The registry phase must first extract ~6 inline arms
   (health/maestros/comms/kill/cancel/empty + team-dot) into methods.

## Risks the plan must respect

- **`routed`/`entity` contract** — web dedup depends on it; preserve exactly.
- **`_pending_new` statefulness** — `/new` flow can't go stateless.
- **Two independent dispatcher instances** in prod (bridge + web) with different
  store bindings (`blueprint_store=None` on bridge) — don't unify or require all
  stores.
- **Coverage floor 75%** — moving untested handlers into new modules needs new
  unit tests in the same PR or coverage drops.
- **Back-ref state rebinding** (ADR 0006) — any introspection-list reset goes
  through `self._dispatcher`, not a local rebind.
- **`empty` is not in `KNOWN_COMMANDS`** but is a dispatch arm — the
  `registry.keys() == KNOWN_COMMANDS` guard must account for `empty`/`cancel`
  (decide: registry includes `empty` or special-case it pre-lookup).
