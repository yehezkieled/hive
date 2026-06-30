# Design — Ticket 045: CommandRouter registry + read-only Formatter split

Chosen approach, the alternatives weighed, and the side-effects. Follows
[ADR 0006](../../adr/0006-god-object-breakup-composition.md) (facade + collaborators).
**No new ADR** — this *applies* 0006, it doesn't decide anything 0006 didn't
already. **No `CONTEXT.md` change** — Formatter/DataStore/GitCommands are internal
class names, not domain glossary terms.

## Target structure

```
  src/hive/commands/
    dispatch.py            CommandDispatcher = FACADE
                             • _ROUTES (the one source of truth) + _registry
                             • dispatch() / dispatch_command()  (public, unchanged)
                             • _send_to_entity, _pending_new flow, _render_personality_md
                             • facade-retained handlers (21): kill/message/agent/task/
                               priority/team/project/mode/loop/swarm/compact/reset/new/
                               cancel/personality/broadcast/model/approve/deny/eval/budget
    result.py    (new)     CommandResult           ← cycle-breaker (no deps)
    _helpers.py  (new)     _strip_quotes, _parse_task_id  ← shared by >1 group
    formatter.py (new)     Formatter           (12 read-only cmds)
    datastore_commands.py (new)  DataStoreCommands  (vault, blueprint)
    git_commands.py (new)  GitCommands         (commit, pr, merge + _worktree_for)
```

### Group membership (from the research dep-matrix)
- **Formatter (12):** status, org, teams, quota, help, maestros, comms, health,
  cost, audit, tasks, files. *(maestros/comms/health were inline in the if-chain
  → lifted into methods.)*
- **DataStoreCommands (2):** vault, blueprint.
- **GitCommands (3):** commit, pr, merge (+ the `_worktree_for` helper).
- **Facade-retained (21 + `empty`):** everything else — lifecycle, messaging,
  approvals, the stateful `/new` flow.

## Decision 1 — the registry mechanism (single source of truth)

A class-level `_ROUTES` dict (`name → (group_attr | None, method_name)`) is the
**one** place a command is declared. The instance `_registry` binds it; the
public `KNOWN_COMMANDS` *derives* from it at module load (so `bridge.py`'s
`from … import KNOWN_COMMANDS` keeps working unchanged):

```python
class CommandDispatcher:
    _ROUTES = {
        "empty":  (None, "_empty"),
        "status": ("formatter", "status"),
        "cost":   ("formatter", "cost"),
        "vault":  ("datastore", "vault"),
        "commit": ("git", "commit"),
        "kill":   (None, "_kill"),            # facade-retained
        # … 39 entries total
    }

    def __init__(self, …):
        self.formatter = Formatter(self.process_manager, self.token_store,
                                   self.audit_log, self.task_store, self.attachment_store)
        self.datastore = DataStoreCommands(self.process_manager, self.vault_store,
                                           self.blueprint_store)
        self.git = GitCommands(self.process_manager, self.audit_log)
        self._registry = {
            name: getattr(getattr(self, grp) if grp else self, meth)
            for name, (grp, meth) in self._ROUTES.items()
        }

    async def dispatch_command(self, cmd, actor="system") -> CommandResult:
        handler = self._registry.get(cmd.name)
        if handler is None:
            return CommandResult(text=f"Unknown command: /{cmd.name}")
        return await handler(cmd, actor)

KNOWN_COMMANDS = frozenset(CommandDispatcher._ROUTES) - {"empty"}   # 38, derived
```

- **Uniform handler shape:** every routed handler is
  `async (cmd: Command, actor: str) -> CommandResult`. The few special shapes
  (`message`/`agent` set `routed=True, entity=…`; `team` does dot-routing before
  its create/list/kill) live *inside* their own handler — `dispatch_command` has
  **no special cases left**.
- **Preserve bodies, wrap thinly.** Existing `_execute_*`/`_format_*` bodies move
  to their group **unchanged** (signatures kept); the routed handler is the old
  if-arm relocated to a named method (e.g. `Formatter.cost(cmd, actor)` →
  `CommandResult(text=await self._format_cost(cmd.args))`). Smallest faithful diff
  for a no-behaviour-change refactor; keeps `test_git_commands.py`'s direct
  `_execute_commit(target, args)` calls valid (now on `dispatcher.git`).
- **Drift guard (new test):** `set(dispatcher._registry) - {"empty"} == KNOWN_COMMANDS`
  and the existing `BRIDGE_COMMANDS`/`HELP_TEXT` bidirectional check
  (`test_help.py`) is re-run. Adding a command is then **one handler + one
  `_ROUTES` line** or the guard fails.

## Decision 2 — collaborators take their deps (DI), not a facade back-ref

ADR 0006's `process/` collaborators hold `self._mgr` (a back-ref) because they
**mutate shared facade state** (`_entities`, `_state_lock`, `_last_routed_actions`).
The command collaborators do **not** — they only read `process_manager`'s *public*
surface + their own stores (verified across all 17 handlers in research). So they
take **constructor-injected dependencies**:

```
  Formatter(process_manager, token_store, audit_log, task_store, attachment_store)
  DataStoreCommands(process_manager, vault_store, blueprint_store)
  GitCommands(process_manager, audit_log)        # + git_ops module, ALLOW_AUTO_MERGE
```

Why DI over back-ref here (the constraint that picks it):
1. **Acceptance demands a standalone Formatter** — a future web read-endpoint must
   build `Formatter(pm, read-only stores)` with **no dispatcher and no
   mutation/approval machinery**. A back-ref to the facade would defeat that.
2. **No facade-private state is touched**, so the back-ref buys nothing.
3. **Isolated unit tests** (acceptance) get trivial: construct the collaborator
   with a mock `ProcessManager` + mock stores, no facade.

This still *follows ADR 0006* in spirit (facade + extracted collaborators + thin
delegation via the registry + one module + isolated test each) — it swaps the
back-ref for DI for a stated reason. Documented here so the deviation is explicit.

## Decision 3 — Formatter store policy (Option A, confirmed with the dev)

`Formatter` holds the **read-only** stores its views need
(`token_store`/`audit_log`/`task_store`/`attachment_store`) and is **forbidden**
the approval/mutation stores (`vault_store`/`mode_request_store`/`blueprint_store`).
The literal "needs only a ProcessManager" is read as "no approval/mutation stores"
— the ticket's own Formatter list already includes `cost`+`audit`, which require
stores. `/tasks` and `/files` join Formatter (read-only views), so GitCommands is
pure write-path (commit/pr/merge). **Zero output change** — every string in the
research's "what the user sees" panel is produced byte-for-byte by the moved code;
the existing `test_command_dispatcher.py` assertions still pin them.

## Alternatives considered

| Alt | Why not |
|-----|---------|
| **Strict PM-only Formatter** (Option B) | Can't render cost/audit/tasks/files (they read stores); would scatter read-only commands back onto the facade and the read-endpoint goal evaporates. |
| **Back-ref collaborators** (literal ADR 0006) | Breaks the standalone-Formatter acceptance; buys nothing since no facade-private state is touched. |
| **Re-signature every `_execute_*` to `(cmd, actor)`** | Larger churn; would force rewriting `test_git_commands.py`'s direct calls and edit 21 handler bodies for no behavioural gain. Thin wrappers are lower-risk. |
| **Fan-out lane (PR per group)** | Every phase rewrites `dispatch.py` → constant rebase collisions. Direct lane, sequential commits. |
| **`/files` stays in Git** (ticket's nominal list) | It has zero git/worktree code — it's an `attachment_store` read. Belongs in Formatter by dependency. |

## Side-effects / cross-cutting

- **No `CONTEXT.md` edit, no new ADR.** (Internal refactor under ADR 0006.)
- **`commands/__init__.py`** re-export of `KNOWN_COMMANDS` stays valid (still
  module-level in `dispatch.py`, now derived).
- **`bridge.py` / `__main__.py` unchanged** — facade constructor signature is
  byte-identical; they keep passing the same 8 stores.
- **Test files:** `test_git_commands.py` repointed to `dispatcher.git`
  (its calls become the Git group's isolated tests); new `test_formatter.py` +
  `test_datastore_commands.py`; `test_command_dispatcher.py` keeps the
  routing/registry + drift-guard + facade-retained + `/new`-flow tests.
