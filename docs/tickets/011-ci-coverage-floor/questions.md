# 011 — Questions

The unknowns going into this ticket. Answered in `research.md` /
`design.md`; kept here as the record of what was open at the start.

1. **What is current line coverage?** The floor is a ratchet "just
   under current", so the exact number is the one fact everything
   else depends on. Measure `pytest -m "not integration" --cov=src/hive`.

2. **Where should the threshold live?** Two valid homes per the
   ticket: `[tool.coverage.report] fail_under` in `pyproject.toml`, or
   a `--cov-fail-under` flag on the CI pytest line. Which gives one
   source of truth?

3. **What margin under current is right?** Too tight and benign churn
   (e.g. deleting a well-tested file) reds the build; too loose and
   real regressions slip through. What buffer absorbs noise without
   inviting rot?

4. **Should hard-to-test files be `omit`-ed to lift the number?**
   `__main__.py`, `cli/local.py`, `process/worktree.py` are near-zero
   coverage and drag the total down. Omitting them would raise the
   floor — but is that an honest gate or a gamed one?

5. **Should the discarded report be surfaced?** The ticket's own
   complaint is the number is "computed and thrown away". Is it worth
   printing missing lines (`term-missing`) or uploading an artifact so
   a failing gate is actionable?

6. **Does `pytest-cov` honour `fail_under` from config and actually
   fail CI?** The gate is worthless if the non-zero exit doesn't
   propagate. Must be proven, not assumed.
