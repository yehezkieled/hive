# Research — `WorkerAgent` → `Worker` rename

Investigated 2026-05-30 by an Explore agent during Phase 2 scoping.
File counts re-verified by direct `grep` on `main` the same day.

## Blast radius — 66 references across 14 files

Verified counts (`grep -c "WorkerAgent"`):

| File | Refs | Role |
|---|---|---|
| `src/hive/models/worker.py` | 1 | class definition |
| `src/hive/bus/entity_store.py` | 6 | import + isinstance checks (worker-specific persistence) |
| `src/hive/process/manager.py` | 12 | import + isinstance checks + type hint on `spawn_worker` |
| `src/hive/commands/dispatch.py` | 3 | import + isinstance + docstring |
| `src/hive/models/team.py` | 1 | docstring |
| `tests/test_entity.py` | 5 | class instantiation in fixtures |
| `tests/test_entity_store.py` | 5 | persistence round-trip tests |
| `tests/test_process_manager.py` | 7 | spawn/lifecycle tests |
| `tests/test_git_commands.py` | 11 | fixture + parametrize over `WorkerAgent` |
| `tests/test_mode_approval.py` | 2 | fixture |
| `tests/test_auto_recovery.py` | 5 | recovery fixtures |
| `tests/test_team.py` | 4 | team-shape tests |
| `tests/test_vault_payment_request.py` | 2 | request-flow tests |
| `tests/integration/test_lead_worker_roundtrip.py` | 2 | end-to-end fixture |

No `__init__.py` exports — every reference is a direct import path.

## Public-API exposure — none

Verified by inspection:

- **No JSON payloads** — entity serialization uses the `role` string
  (`"worker"`), not the class name.
- **No Telegram surface** — class name never appears in help text or
  command output (checked `telegram/help_text.py`, `commands.py`).
- **No persisted state coupling** — `EntityStore` reconstructs by
  `role == "worker"` (`entity_store.py:163`), not by class name.
- **No dashboard URL coupling** — no `web/` reference to the class
  name.
- **No log strings** — logs reference `entity.role`, not `__class__`.

Conclusion: rename is **entirely internal**. No breaking surface for
users, JSON consumers, or persisted state.

## Sibling-class naming

| Class | Suffix |
|---|---|
| `Maestro(Entity)` | none |
| `TeamLead(Entity)` | none |
| `WorkerAgent(Entity)` | `*Agent` (outlier) |

Renaming this one class produces a consistent set: `Maestro`,
`TeamLead`, `Worker`.

## String-literal coupling — none required

The role string `"worker"` (lowercase) appears in 30+ locations:

- `entity_store.py:163` — `if role == "worker":` (reconstruction)
- `manager.py:326, 331, 1448, 1611` — role-based branching
- `permissions.py:52, 64, 97, 117` — permission checks
- `models/entity.py:162, 264` — role enum validation
- `claude_adapter.py:29, 103, 130` — adapter role validation
- `loops.py:8` — `_VALID_ROLES = ("maestro", "lead", "worker", "vault")`
- Command/Telegram/test files — CLI references

All of these are already decoupled from the class name. The rename
does **not** touch a single `"worker"` string.

## Migration risk — zero

- DB stores `role = "worker"` (text), not class name
  (`entity_store.py` lines 63–64).
- Entity reconstruction via role string (line 163), not class
  inspection.
- No pickle / dill serialization of class objects.
- No JSON serialization of `__class__`.

No DB migration. No state rehydration. No backfill.

## Estimated effort

3–4 hours including validation. Mechanical change.

## Why not also rename the siblings?

Siblings are already consistent — `Maestro`, `TeamLead` — no `*Agent`
suffix. Renaming them is unnecessary. Renaming just the outlier
brings the codebase to a consistent state.
