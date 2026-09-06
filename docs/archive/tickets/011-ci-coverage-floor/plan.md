# Plan — Ticket 011: Add a CI coverage floor

Direct lane — one PR. Add a `fail_under` ratchet to coverage config and
point CI at it. Current coverage measured at **77.36%**; floor set to
**75**. Mechanism verified (see `research.md` Q6).

## Files this Ticket creates / modifies

| Path | Op | Step |
|------|----|------|
| `pyproject.toml` | modify | Add `[tool.coverage.run] source = ["src/hive"]` and `[tool.coverage.report] fail_under = 75`. |
| `.github/workflows/ci.yml` | modify | Change the Test step to `pytest -m "not integration" --cov --cov-report=term-missing`. |

### Exact changes

**`pyproject.toml`** — append after the existing `[tool.pytest.ini_options]` block:

```toml
[tool.coverage.run]
source = ["src/hive"]

[tool.coverage.report]
fail_under = 75
```

**`.github/workflows/ci.yml`** — the Test step (line 28–29):

```yaml
      - name: Test
        run: pytest -m "not integration" --cov --cov-report=term-missing
```

(was `run: pytest -m "not integration" --cov=src/hive` — the
`=src/hive` target moves into `[tool.coverage.run] source`.)

## Verification

1. **Gate trips below floor** (proven in research, re-confirm post-edit):
   temporarily set `fail_under = 99`, run
   `pytest -m "not integration" --cov` → expect non-zero exit +
   `FAIL Required test coverage of 99.0% not reached`. Restore to 75.
2. **Gate passes at floor**: `pytest -m "not integration" --cov` with
   `fail_under = 75` → exit 0, prints
   `Required test coverage of 75% reached. Total coverage: 77.3x%`.
3. **`term-missing` prints**: the run's terminal report shows the
   `Missing` column with uncovered line ranges.
4. **Lint clean**: `ruff check src/ tests/ && ruff format --check src/ tests/`
   (pyproject is not Python-formatted by ruff, but run the gate anyway).
5. **CI green on the PR**: the live CI run passes with the floor active.

## Out of scope

- Raising coverage on the low files (`dispatch.py`, `__main__.py`,
  `cli/local.py`, `process/worktree.py`) — the floor is a regression
  alarm, not a campaign to lift the number.
- Codecov / XML artifact upload or a coverage trend dashboard — deferred
  (see `design.md`).
- `omit`-ing hard-to-test files — rejected (honest floor over gamed).

## Cross-cutting impact

- **None.** No reference doc (`README` / `DEPLOYMENT.md`), `CONTEXT.md`,
  or ADR is touched — the CI workflow is not a reference doc and runtime
  behaviour is unchanged.

## Ratchet policy

`fail_under` is a floor: raise it when coverage climbs durably, never
lower it to make a red build pass. (See `design.md`.)

## To build

Direct lane — apply the two edits above on a branch, run the
verification, open one PR. No fleet, no Workflow.
