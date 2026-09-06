# 011 — Research

Code-grounded answers to `questions.md`. All numbers measured on this
worktree at commit `90c78e7` (same tree as `main`), 2026-06-07.

## Q1 — Current coverage: **77.36%**

```
pytest -m "not integration" --cov=src/hive --cov-report=term-missing
→ 1107 passed, 2 skipped in ~4m50s
→ TOTAL  6050 stmts  1370 miss  77%   (exact 4680/6050 = 77.36%)
```

The displayed `77%` is rounded; the true ratio is **77.36%**. This is
the denominator the floor is set against.

### What drags the total down (all known gaps, not regressions)

| File | Stmts | Cover | Why it's low |
|------|------:|------:|--------------|
| `commands/dispatch.py` | 829 | 39% | The 1.3k-LOC god object, flagged for a 004-style breakup next cycle |
| `__main__.py` | 186 | 22% | Process entrypoint / wiring — exercised by the live service, not units |
| `cli/local.py` | 83 | 0% | Dev-only local CLI, entirely untested |
| `process/worktree.py` | 54 | 19% | Thin `git worktree` shell wrapper |
| `mcp/advisor_server.py` | 131 | 54% | MCP server glue |
| `telegram/bridge.py` | 236 | 74% | Telegram I/O edges |

Every other module sits 81–100%. So 77% is an **honest** floor — it is
not inflated by dead or excluded code, and it is dragged down by a few
known-hard-to-unit-test modules, not by a thin suite overall.

## Q2 / Q3 — Threshold home and margin

`pytest-cov` reads `coverage.py` config from `pyproject.toml` under
`[tool.coverage.*]`. Two homes are possible:

| Option | Enforced where | Single source of truth? |
|--------|----------------|-------------------------|
| `[tool.coverage.report] fail_under` (pyproject) | local `pytest --cov` **and** CI | yes — "documented in one place" per acceptance |
| `--cov-fail-under=N` (ci.yml flag) | CI only | no — dev sees green locally, CI rejects |

Chosen: **pyproject config**. Adding `[tool.coverage.run] source =
["src/hive"]` lets the CI command drop the explicit `--cov=src/hive`
target — both the source and the floor then live in one config block.

Margin: **floor = 75** (~2.4 pts under 77.36). Rationale in `design.md`;
in short, it absorbs ordinary churn noise while still tripping on a
meaningful (~100-stmt) untested module landing.

## Q4 — `omit` the low files? **No.**

Omitting `__main__.py` / `cli/local.py` / `process/worktree.py` would
lift the headline number, but it hides real gaps and lets untested code
grow silently behind the exclusion. An honest 75 floor on the full
package is a better gate than a gamed 85 on a curated subset.

## Q5 — Surface the report? **Yes, cheaply.**

Add `--cov-report=term-missing` to the CI command so a failing gate
prints exactly which lines are uncovered — directly answers the
"computed and thrown away" complaint at zero cost. Full Codecov / XML
artifact upload is heavier and out of scope for a hardening ticket.

## Q6 — Does `fail_under` from config actually fail CI? **Proven: yes.**

Verified with a temp `--cov-config` on one fast test file (so no 5-min
suite needed), against real `src/hive`:

```
[run]    source = src/hive
[report] fail_under = 99   → pytest exit 1   "FAIL Required test coverage of 99.0% not reached"
[report] fail_under = 10   → pytest exit 0   "Required test coverage of 10.0% reached"
```

Two confirmations from this:
1. **`fail_under` in config trips a non-zero pytest exit** — CI will
   red on a drop below the floor.
2. **Bare `--cov` reads `source` from `[tool.coverage.run]`** — it
   measured the full 6050-stmt package from one test file, so the CI
   command needs no explicit `--cov=src/hive` once `source` is set.
