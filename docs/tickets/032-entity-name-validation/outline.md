# Outline — Ticket 032: Validate entity/team names

## New module: `src/hive/process/names.py`

```python
import re

_NAME_RE = re.compile(r"[A-Za-z0-9_-]+")   # one component (no dot)
_MAX_LEN = 64

def validate_name(name: str, *, kind: str) -> None:
    # 1. non-empty / not whitespace      -> "<kind> cannot be empty"
    # 2. len(name) <= _MAX_LEN           -> "too long (max 64)"
    # 3. not name.startswith("-")        -> "cannot start with '-'"
    # 4. _NAME_RE fullmatch(name)        -> "'<char>' not allowed — letters,
    #                                         digits, '-', '_' only"
    # raises ValueError(f"Invalid {kind} '{name}': <reason>")
```
Reporting the **first offending character** (step 4) makes the error
actionable.

## Wire-in (2 call sites + 1 feedback)

1. `lifecycle_manager.register_maestro` — `validate_name(name, kind="maestro
   name")` at the top, before the duplicate check.
2. `lifecycle_manager.create_team` — `validate_name(team_name, kind="team
   name")` at the top, **before** `entity.create_team(...)` (line ~395) and the
   worktree/branch creation (line ~404).
3. `message_dispatcher.py` `spawn_team` `except` (~620) — collect the
   `ValueError` text and route it back to the maestro via the existing
   `_handle_parse_errors` feedback path, instead of `logger.warning` only.

## Tests

`tests/process/test_names.py` (new) — pure unit tests of `validate_name`:
- **accept:** `otter`, `dev`, `hive_dev`, `my-team`, `Otter`, `a`, 64-char name.
- **reject:** `""`, `"   "`, `my team`, `a/b`, `.`, `..`, `back.end`, `-rf`,
  `"a"*65`, `a;b`, `a$b`, `a\tb`, `..\.\.` — each with its reason.

Touch existing suites:
- `tests/process/test_lifecycle_manager.py` — `create_team` / `register_maestro`
  raise on a bad name **before** `worktree_mgr.create` is called (assert the
  mock was not invoked).
- `tests/process/test_message_dispatcher.py` — `spawn_team` with a bad name →
  maestro receives feedback (assert the feedback path fires), no team
  registered.

## Verification

```
ruff check src/ tests/ && ruff format --check src/ tests/
pytest -m "not integration"
```
Manual smoke: `/team create "my team"` → clear rejection, no worktree/branch
made; `/team create my-team` → succeeds.
