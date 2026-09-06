# Plan — Ticket 032: Validate entity/team names  (issue #186)

**Lane:** direct (one issue, one PR). Single, well-bounded change — a shared
validator + two wire-in points + one feedback fix.

## Files this Ticket creates / modifies

| Path | Op | Step |
|------|----|------|
| `src/hive/process/names.py` | create | `validate_name(name, *, kind)` — allowlist `[A-Za-z0-9_-]`, non-empty, no leading `-`, len ≤ 64; raises `ValueError` naming the offending char |
| `src/hive/process/lifecycle_manager.py` | modify | validate at the top of `register_maestro` (maestro name) and `create_team` (team name), **before** any worktree/branch derivation |
| `src/hive/process/message_dispatcher.py` | modify | `spawn_team` `except` (~620): route the rejection back to the maestro via `_handle_parse_errors`, not `logger.warning` only |
| `tests/process/test_names.py` | create | unit tests — accept valid names, reject every boundary case |
| `tests/process/test_lifecycle_manager.py` | modify | assert rejection happens **before** `worktree_mgr.create` is called |
| `tests/process/test_message_dispatcher.py` | modify | assert the maestro gets feedback on a rejected `spawn_team` |
| `CONTEXT.md` | (done) | glossary: **Entity name** (component vs address) — already committed in this artifact set |

## Verification

- `ruff check src/ tests/ && ruff format --check src/ tests/`
- `pytest -m "not integration"` green (new `test_names.py` + touched suites)
- Manual smoke on deployed code: `/team create "my team"` → clear rejection,
  **no** worktree/branch created; `/team create my-team` → succeeds; a maestro
  `spawn_team` with a bad name gets told why.

## Out of scope

- CC slug handling (Ticket 030).
- Renaming / migrating existing entities.
- Path-derivation redesign (worktree layout, branch scheme) — validation only.
- Case-folding duplicate detection (case is preserved; duplicates stay
  exact-match).

## Cross-cutting impact

- `CONTEXT.md` glossary entry (**Entity name**) — committed with this set.
- No ADR (small, reversible; see `design.md`).

## Build

One branch (`ticket-032/...`), one PR that closes #186.
