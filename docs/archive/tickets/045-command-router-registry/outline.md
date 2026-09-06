# Outline — Ticket 045: CommandRouter registry + read-only Formatter split

Implementation structure for the **direct lane** (one PR, four reviewable
commits). Each commit is independently green (`ruff` + `pytest -m "not
integration"`). Sequencing resolves the "registry-first" tension: a registry that
maps to `self.formatter.x` needs the collaborators to exist, so **commit 1 builds
the registry with every handler still on the facade** (`_ROUTES` all `(None, …)`),
then commits 2–4 relocate each group and flip its route target.

## Invariants held across all four commits
- `CommandDispatcher.__init__` signature **byte-identical** (8 stores + PM +
  defaults). `bridge.py` / `__main__.py` untouched.
- `dispatch()` / `dispatch_command()` public; `CommandResult` fields
  (`text/metadata/routed/entity`) unchanged; `routed` semantics preserved.
- `_pending_new` `/new`-flow state stays on the facade.
- Every read-only command's **output string is byte-for-byte identical**
  (existing `test_command_dispatcher.py` assertions stay as-is).
- `KNOWN_COMMANDS` stays importable from `hive.commands.dispatch`.

## Commit 1 — Registry + scaffolding (mechanical, no extraction)
*Goal: kill the 39-arm if-chain; everything still lives on the facade.*
- **New `commands/result.py`** — move `CommandResult` out (cycle-breaker for the
  group modules to come). `dispatch.py` re-imports it.
- **New `commands/_helpers.py`** — move shared `_strip_quotes`, `_parse_task_id`
  (used by >1 future group + facade).
- **Lift the ~6 inline arms** (`empty`, `health`, `maestros`, `comms`, `kill`,
  `cancel`) into facade methods.
- **Add a uniform handler per command** — `async (cmd, actor) -> CommandResult`,
  each wrapping the preserved `_execute_*`/`_format_*` body (the old if-arm,
  relocated). Fold `team`'s dot-routing into the `team` handler; `message`/`agent`
  set `routed=True, entity=…` inside their handlers.
- **Add `_ROUTES`** (class attr, all `(None, method)`), build `self._registry` in
  `__init__`, replace the if-chain in `dispatch_command` with the dict lookup +
  unknown-command fallback.
- **Derive** `KNOWN_COMMANDS = frozenset(_ROUTES) - {"empty"}`.
- **New test** — `test_command_dispatcher.py`: registry/`KNOWN_COMMANDS` drift
  guard (`set(_registry) - {"empty"} == KNOWN_COMMANDS`). Re-run `test_help.py`.

## Commit 2 — Extract Formatter (12 read-only commands)
*status / org / teams / quota / help / maestros / comms / health / cost / audit /
tasks / files*
- **New `commands/formatter.py`** — `Formatter(process_manager, token_store,
  audit_log, task_store, attachment_store)`. Move the 12 handler bodies + their
  *private* helpers (`_parse_window`/`_Window`, `_parse_audit_args`/
  `_format_audit_row`, `_format_task_row`, `_format_bytes`).
- Facade `__init__` builds `self.formatter = Formatter(self.process_manager,
  self.token_store, self.audit_log, self.task_store, self.attachment_store)`;
  flip those 12 `_ROUTES` entries to `("formatter", …)`.
- `_execute_team` "list" subcommand → `self.formatter._format_teams()`.
- **New `tests/test_formatter.py`** — isolated, mock `ProcessManager` + read-only
  stores; assert each view string. Confirms the Formatter constructs **without**
  vault/mode_request/blueprint.

## Commit 3 — Extract DataStoreCommands (vault, blueprint)
- **New `commands/datastore_commands.py`** — `DataStoreCommands(process_manager,
  vault_store, blueprint_store)`. Move `_execute_vault`, `_execute_blueprint`;
  import `_strip_quotes`/`_parse_task_id` from `_helpers.py`.
- Facade builds `self.datastore`; flip vault/blueprint `_ROUTES` to `("datastore",…)`.
- **New `tests/test_datastore_commands.py`** — mock PM + vault/blueprint stores;
  cover the approve/deny/status/log + save/search/list subcommands.

## Commit 4 — Extract GitCommands (commit, pr, merge)
- **New `commands/git_commands.py`** — `GitCommands(process_manager, audit_log)`.
  Move `_execute_commit`, `_execute_pr`, `_execute_merge`, `_worktree_for`; uses
  `git_ops` + `ALLOW_AUTO_MERGE`.
- Facade builds `self.git`; flip commit/pr/merge `_ROUTES` to `("git", …)`.
- **Repoint `tests/test_git_commands.py`** — `bridge.dispatcher._execute_commit(…)`
  → `bridge.dispatcher.git._execute_commit(…)` (×10). This file becomes the Git
  group's isolated unit test (acceptance: per-group tests).

## Post-extraction shape
`dispatch.py` ≈ facade (registry + `dispatch`/`dispatch_command`) + 21
facade-retained handlers + `_send_to_entity` + `/new` flow + `_render_personality_md`
— well under half the original 1367 LOC; the three group modules carry the rest.

## Verification (every commit + final)
```
ruff check src/ tests/ && ruff format --check src/ tests/
pytest -m "not integration"            # full command suite green
pytest --cov=src/hive/commands -m "not integration"   # coverage ≥ 75 floor (T011)
```
Behaviour check: the read-only command outputs match the research's "what the user
sees" panel exactly (the assertions enforce it).
