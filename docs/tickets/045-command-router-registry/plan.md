# Plan — Ticket 045: CommandRouter registry + read-only Formatter split  (issue #243)

**Lane:** direct (one PR, four reviewable commits — see `outline.md`). Pure
decomposition under [ADR 0006](../../adr/0006-god-object-breakup-composition.md);
**no behaviour or output change**. Build with one agent/branch; the build PR
closes #243.

## Files this Ticket creates / modifies

| Path | Op | Commit | Notes |
|------|----|--------|-------|
| `src/hive/commands/result.py` | create | 1 | `CommandResult` moved out (cycle-breaker) |
| `src/hive/commands/_helpers.py` | create | 1 | shared `_strip_quotes`, `_parse_task_id` |
| `src/hive/commands/dispatch.py` | modify | 1–4 | facade: `_ROUTES` + `_registry`, if-chain → lookup, lift inline arms, derive `KNOWN_COMMANDS`, build collaborators in `__init__`; bodies relocate out in 2–4 |
| `src/hive/commands/formatter.py` | create | 2 | `Formatter(pm, token_store, audit_log, task_store, attachment_store)` + 12 read-only handlers + their private fmt helpers |
| `src/hive/commands/datastore_commands.py` | create | 3 | `DataStoreCommands(pm, vault_store, blueprint_store)` — vault/blueprint |
| `src/hive/commands/git_commands.py` | create | 4 | `GitCommands(pm, audit_log)` — commit/pr/merge + `_worktree_for` |
| `tests/test_command_dispatcher.py` | modify | 1 | add registry/`KNOWN_COMMANDS` drift-guard test; existing assertions unchanged |
| `tests/test_formatter.py` | create | 2 | isolated: mock PM + read-only stores; assert each view + no-mutation-store construction |
| `tests/test_datastore_commands.py` | create | 3 | isolated: mock PM + vault/blueprint stores |
| `tests/test_git_commands.py` | modify | 4 | repoint `dispatcher._execute_*` → `dispatcher.git._execute_*` (×10) |

**Untouched (verify, don't edit):** `src/hive/telegram/bridge.py`,
`src/hive/__main__.py`, `src/hive/commands/__init__.py`,
`src/hive/telegram/help_text.py` — the facade constructor + `KNOWN_COMMANDS`
export contracts are preserved, so these keep working as-is.

## Verification

Per commit and at the end:
```
ruff check src/ tests/ && ruff format --check src/ tests/
pytest -m "not integration"                              # full command suite green
pytest --cov=src/hive/commands -m "not integration"      # coverage ≥ 75 floor (Ticket 011)
```
- **Behaviour parity:** read-only command outputs match `research.md`'s "what the
  user sees" panel byte-for-byte (locked by existing `test_command_dispatcher.py`
  assertions + `test_help.py` drift guard).
- **Standalone Formatter proof:** `test_formatter.py` constructs `Formatter` with
  **no** vault/mode_request/blueprint store and renders cost/audit/tasks/files.
- **Routing proof:** drift-guard test asserts `set(_registry) - {"empty"} ==
  KNOWN_COMMANDS`; no if-chain remains in `dispatch_command`.

## Out of scope
- Any behaviour/output change (pure decomposition).
- A multi-transport command-adapter layer (Telegram/Web/CLI) — Phase 5 / a
  Telegram-cleanup ticket.
- The ActionRouter / `message_dispatcher` decomposition (audit #1, roadmap Phase 6).
- Extracting the approval (`approve`/`deny`) or lifecycle groups — they stay
  facade-retained this ticket.

## Cross-cutting impact
- **None to reference docs.** No `CONTEXT.md` edit (internal class names, not
  glossary), **no new ADR** (applies ADR 0006, decides nothing new).
- INDEX row flips to `in progress` with issue `#243` (done at ship).

## Build handoff
Direct lane — build as a single PR (one agent/branch) following the four commits
in `outline.md`, validating the gate above per commit. The build PR closes #243.
