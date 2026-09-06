# 011 — Design

## Chosen approach

Move coverage configuration into `pyproject.toml` and set a ratchet
floor; simplify the CI command to consume it.

```toml
# pyproject.toml — new blocks
[tool.coverage.run]
source = ["src/hive"]

[tool.coverage.report]
fail_under = 75
```

```yaml
# .github/workflows/ci.yml — Test step
- name: Test
  run: pytest -m "not integration" --cov --cov-report=term-missing
```

- `source = ["src/hive"]` — the measured package now lives in config,
  so bare `--cov` measures it (proven in `research.md` Q6).
- `fail_under = 75` — the ratchet. Current is 77.36%; 75 sits ~2.4 pts
  under. `pytest-cov` exits non-zero below this, failing CI.
- `--cov-report=term-missing` — prints uncovered lines in the CI log so
  a tripped gate is actionable (answers the "thrown away" complaint).

Single source of truth: both the floor and the measured package live in
one `pyproject.toml` block, enforced identically by local `pytest --cov`
and CI.

## Why 75 (the ratchet margin)

```
            floor = 76 (tight)              floor = 75 (chosen)
headroom    ~1.4 pts                        ~2.4 pts
catches     small regressions early         meaningful regressions (~100 stmts)
risk        a benign refactor that deletes  absorbs ordinary churn noise;
            well-tested code can dip <76     a real untested module still trips it
```

A coverage floor is a **regression alarm, not a target**. Its job is to
catch a meaningful chunk of untested code landing, not to police every
fractional wobble. 76 invites false reds on harmless churn (the 004/007
refactors that prompted this ticket are exactly the kind of code-moving
work that nudges the number around); 75 leaves enough slack to ride that
noise while still firing on a genuine drop. 77 (the rounded current) is
strictest but, at precision 0, anything under ~76.5% fails — highest
false-positive rate.

## Ratchet policy (going forward)

The floor is a **floor, never a ceiling, and never lowered to make a red
build pass.** When coverage climbs durably, raise `fail_under` to lock
the gain in. Lowering it to unblock a PR defeats the entire mechanism —
fix the test gap instead, or justify an `omit` in review.

## Alternatives considered

| Option | Verdict | Why |
|--------|---------|-----|
| `--cov-fail-under=75` flag in `ci.yml` only | rejected | CI-only; local `pytest --cov` wouldn't enforce it → dev sees green, CI rejects. Not one source of truth. |
| `omit` the low-coverage files to lift the number | rejected | Hides real gaps; lets untested code grow behind the exclusion. Honest 75 > gamed 85. |
| Floor at 76 / 77 | rejected | Too tight for a churn-heavy codebase; false reds on benign refactors. |
| Codecov / XML artifact upload | deferred | Heavier (external service / artifact plumbing) than a hardening ticket warrants. `term-missing` already makes a failure actionable. Revisit if a coverage trend dashboard is wanted. |

## Side effects

- **`CONTEXT.md`** — none. No new domain term ("coverage floor" is
  generic tooling vocabulary, not Hive language).
- **`docs/adr/`** — none. A CI ratchet is a tooling choice, not an
  architectural decision of ADR weight; the rationale lives here.
- **Reference docs** (`README` / `DEPLOYMENT.md`) — none. The CI
  workflow is not a reference doc, and the change doesn't touch deploy
  or runtime behaviour. Not a cross-cutting ticket.
