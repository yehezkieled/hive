# Plan

Skipped artifacts: `questions`, `design`, `outline` — no open
questions, one obvious approach, no design fork.

## Approach

Single-shot rename. One PR, one commit. Use a mechanical
search-and-replace on `WorkerAgent` → `Worker` across the 14 files
listed below. Do **not** touch the `"worker"` string literal — those
are role values and remain unchanged.

Order: the class definition first (so import paths fail loudly if any
reference is missed), then sites by ascending coupling so the test
suite can be re-run incrementally to catch surprises.

## Steps

1. **Edit `src/hive/models/worker.py`** — rename the class:
   ```python
   class WorkerAgent(Entity):   →   class Worker(Entity):
   ```
   Update the docstring at the top of the file if it says
   "WorkerAgent" or "worker agent" — re-word to "Worker".

2. **Update production imports** (4 files, all use the form
   `from hive.models.worker import WorkerAgent`):
   - `src/hive/bus/entity_store.py`
   - `src/hive/process/manager.py`
   - `src/hive/commands/dispatch.py`
   - (any other production file that imports it — re-run
     `grep -rln "WorkerAgent" src/` to confirm)

3. **Update `isinstance(...)` checks and type hints** in those same
   four production files plus `src/hive/models/team.py`:
   - `isinstance(e, WorkerAgent)` → `isinstance(e, Worker)`
   - `: WorkerAgent` → `: Worker` (parameter and return types)
   - Docstring mentions → "Worker"

4. **Update test files** (10 files):
   ```
   tests/test_entity.py
   tests/test_entity_store.py
   tests/test_process_manager.py
   tests/test_git_commands.py
   tests/test_mode_approval.py
   tests/test_auto_recovery.py
   tests/test_team.py
   tests/test_vault_payment_request.py
   tests/integration/test_lead_worker_roundtrip.py
   ```
   Each contains imports, fixture annotations, and constructor calls
   — all mechanical `WorkerAgent` → `Worker` substitutions.

5. **Run lint + format:**
   ```
   ruff check src/ tests/
   ruff format --check src/ tests/
   ```
   Fix any remaining issues. Lint failure is acceptable to fix
   in-band; format failure means run `ruff format src/ tests/` once
   to re-flow.

6. **Run the full test suite:** `pytest`. Expected: green. Any
   failure here means a reference was missed — `grep` the failing
   test for `WorkerAgent`.

7. **Final check — zero `WorkerAgent` references remain:**
   ```
   grep -rn "WorkerAgent" --include="*.py" src/ tests/
   ```
   Must return zero matches. Run before committing.

8. **Sanity-check blast radius:**
   ```
   git diff --stat
   ```
   Expect ≈ 14 files changed. Any other file in the diff is a bug.

9. **Smoke test:** restart `hive.service`, send a maestro turn,
   confirm the maestro can spawn a worker and the worker completes a
   task end-to-end. Logs should show `Worker` in any
   freshly-emitted class-name messages.

10. **Close the ticket:** update `docs/tickets/INDEX.md` row for 006
    from `in progress` → `done`. Do not add a CHANGELOG entry — that's
    a per-sprint summary at sprint close.

## Validation summary

| Check | Command | Pass criterion |
|---|---|---|
| Lint | `ruff check src/ tests/` | exit 0 |
| Format | `ruff format --check src/ tests/` | exit 0 |
| Tests | `pytest` | green, no skips beyond baseline |
| Zero refs | `grep -rn "WorkerAgent" --include="*.py" src/ tests/` | empty output |
| Blast | `git diff --stat` | ≈ 14 files |
| Smoke | restart hive.service; maestro turn | worker spawns + completes |

## Cross-cutting impact

None. No `README.md`, `DEPLOYMENT.md`, or `ARCHITECTURE.md` edits.
`CONTEXT.md` already uses `Worker` — its "Flagged ambiguities" line
about `WorkerAgent` should be removed at sprint close, not by this
ticket (it's a glossary touch-up, not part of the rename work).

## Rollback

`git revert` the single commit. The rename is one consistent change
across 14 files; revert is symmetric. Nothing else flips behaviour.
